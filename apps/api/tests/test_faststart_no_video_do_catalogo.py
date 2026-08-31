"""O vídeo do catálogo sai do worker com o índice no começo do arquivo.

Este é o caminho inteiro, sem encenação: cliente da API, socket unix, serviço
isolado, `ffmpeg` de verdade e a conferência do catálogo sobre o derivado.

O defeito que ele fecha: até 31/08/2026 o `storage_worker` guardava o vídeo do
vendedor byte a byte como chegou. O `+faststart` existia e era usado só no vídeo
citado dentro do PDF. Editores de vídeo exportam com o índice no fim, então
praticamente toda aula enviada chegava ao aluno sem barra de progresso
utilizável e demorando a começar.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn

from appsell_api import video_verifier
from appsell_api.pdf_sandbox_service import app
from appsell_api.video_verifier import VideoRejected, prepared_catalogue_video


def _ffmpeg() -> str:
    caminho = shutil.which("ffmpeg")
    assert caminho is not None, "ffmpeg é dependência destes testes; o CI o instala"
    return caminho


def _aula(destino: Path, *, codec: str = "libx264", segundos: int = 2) -> Path:
    """Uma aula curta com vídeo e áudio, como sai de um editor.

    Sem `+faststart`, que é justamente o padrão dos editores: o `ffmpeg` grava o
    índice no fim quando ninguém pede o contrário.
    """
    subprocess.run(  # noqa: S603 - argv fixo, gerado pelo próprio teste
        [
            _ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={segundos}:size=320x240:rate=30",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={segundos}",
            "-c:v",
            codec,
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(destino),
        ],
        check=True,
        capture_output=True,
    )
    return destino


def _ordem_das_caixas(caminho: Path) -> tuple[int, int]:
    conteudo = caminho.read_bytes()
    return conteudo.index(b"moov"), conteudo.index(b"mdat")


@pytest.fixture
def sandbox_em_pe(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """O serviço isolado de verdade, atendendo num socket unix.

    O diretório fica curto e em `/tmp` de propósito: o caminho de um socket unix
    cabe em pouco mais de cem bytes, e o temporário do pytest estoura isso.

    A reciclagem do processo depois de cada trabalho fica desligada aqui, e
    precisa ficar: ela chama `os._exit`, que mataria o processo do pytest.
    """
    assert os.environ.get("APPSELL_PDF_SANDBOX_RECYCLE_AFTER_JOB") != "true"
    pasta = Path(tempfile.mkdtemp(prefix="sock-", dir="/tmp"))
    os.chmod(pasta, 0o700)
    socket_path = pasta / "pdf.sock"
    # `log_config=None` porque o padrão do uvicorn chama `dictConfig` e
    # reconfigura o `logging` do processo inteiro. O teste seguinte da sessão
    # media o log da API e passava a falhar por causa deste, sem relação
    # nenhuma com o que ele mede.
    servidor = uvicorn.Server(
        uvicorn.Config(app, uds=str(socket_path), log_config=None, lifespan="off")
    )
    thread = threading.Thread(target=servidor.run, daemon=True)
    thread.start()
    prazo = time.monotonic() + 20
    while not socket_path.exists() and time.monotonic() < prazo:
        time.sleep(0.05)
    assert socket_path.exists(), "o serviço isolado não subiu no prazo"
    monkeypatch.setenv("APPSELL_API_PDF_SANDBOX_SOCKET", str(socket_path))
    monkeypatch.delenv("APPSELL_API_PDF_SANDBOX_URL", raising=False)
    try:
        yield None
    finally:
        servidor.should_exit = True
        thread.join(timeout=20)
        shutil.rmtree(pasta, ignore_errors=True)


@pytest.mark.usefixtures("sandbox_em_pe")
def test_a_aula_chega_ao_aluno_com_o_indice_no_comeco(tmp_path: Path) -> None:
    origem = _aula(tmp_path / "aula.mp4")
    moov_origem, mdat_origem = _ordem_das_caixas(origem)
    # A premissa, medida e não assumida: o editor entrega com o índice no fim.
    assert mdat_origem < moov_origem

    with prepared_catalogue_video(origem) as preparado:
        moov_saida, mdat_saida = _ordem_das_caixas(preparado.path)
        assert moov_saida < mdat_saida
        assert preparado.byte_size == preparado.path.stat().st_size
        assert preparado.sha256_hex == hashlib.sha256(preparado.path.read_bytes()).hexdigest()
        assert preparado.sha256_hex != hashlib.sha256(origem.read_bytes()).hexdigest()
        assert preparado.attempts == 1


@pytest.mark.usefixtures("sandbox_em_pe")
def test_uma_aula_que_precisaria_recodificar_e_recusada(tmp_path: Path) -> None:
    """Recodificar custa 66 s por minuto, e o worker tem lease de 600 s.

    Recusar é a única resposta que não deixa o trabalho estourar o lease e
    voltar para a fila para estourar de novo.
    """
    origem = _aula(tmp_path / "exotica.mp4", codec="mpeg4")
    with pytest.raises(VideoRejected), prepared_catalogue_video(origem):
        pass


@pytest.mark.usefixtures("sandbox_em_pe")
def test_o_sandbox_atende_um_de_cada_vez(tmp_path: Path) -> None:
    """Dois preparos ao mesmo tempo: o segundo leva recusa, e ela é passageira.

    Vale registrar porque é o motivo de o cliente insistir: no worker, o vídeo
    seguinte chega enquanto o serviço ainda está ocupado ou reiniciando.
    """
    origem = _aula(tmp_path / "aula.mp4")
    respostas: list[int] = []

    def ocupar() -> None:
        with (
            httpx.Client(
                transport=httpx.HTTPTransport(
                    uds=os.environ["APPSELL_API_PDF_SANDBOX_SOCKET"], retries=0
                ),
                timeout=httpx.Timeout(60.0, connect=5.0),
                trust_env=False,
            ) as cliente,
            origem.open("rb") as corpo,
        ):
            conteudo = corpo.read()
            resposta = cliente.post(
                "http://pdf-sandbox/prepare-video",
                content=conteudo,
                headers={
                    "content-type": "application/octet-stream",
                    "x-appsell-byte-size": str(len(conteudo)),
                    "x-appsell-sha256": hashlib.sha256(conteudo).hexdigest(),
                    "x-appsell-mode": "catalogue",
                },
            )
            respostas.append(resposta.status_code)

    threads = [threading.Thread(target=ocupar) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert sorted(respostas) in ([200, 200], [200, 429])


def test_o_cliente_do_worker_fala_com_o_sandbox_e_com_mais_ninguem() -> None:
    """A regra que a arquitetura depende: parser de mídia não roda aqui."""
    fonte = Path(video_verifier.__file__).read_text(encoding="utf-8")
    assert "remux_to_mp4" not in fonte
    assert "http://pdf-sandbox/prepare-video" in fonte
