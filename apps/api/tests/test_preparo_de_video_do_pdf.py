"""Como os vídeos do PDF são preparados, e o que o preparo não pode atrapalhar.

O vídeo que o PDF cita é baixado da origem e convertido para MP4 num serviço
isolado, que fica guardado no storage. Duas coisas separam esse trabalho em duas
filas diferentes, e é isso que estes testes guardam:

- o **download** é da API e domina o tempo — dois de cada vez, porque
  serializá-lo fazia o segundo aluno esperar o download inteiro do primeiro;
- a **conversão** é do serviço isolado, que atende um arquivo por vez e recusa
  quem chega junto, com `429`. A fila fica aqui, onde esperar não custa nada.
"""

from __future__ import annotations

import asyncio
import threading
import time
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from anyio import to_thread

from appsell_api import learning_routes, pdf_link_video
from appsell_api.pdf_link_video import derivative_key, trusted_video_target
from appsell_api.storage_s3 import S3Storage

RAIZ = "https://tcplgbtdodjlvforijdd.supabase.co/storage/v1/object/public/aulas"
ORIGEM = f"{RAIZ}/um.mov"
VENDEDOR = "9f1c2f3e-4d5a-4b6c-8d7e-0a1b2c3d4e5f"


@pytest.fixture(autouse=True)
def _estado_limpo(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv(
        "APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", "https://tcplgbtdodjlvforijdd.supabase.co"
    )
    learning_routes._PDF_LINK_VIDEO_TASKS.clear()
    # Os semáforos são globais do módulo, e recriados a partir das constantes
    # dele — um número escrito aqui esconderia justamente a mudança que estes
    # testes existem para vigiar.
    monkeypatch.setattr(
        learning_routes,
        "_PDF_LINK_VIDEO_SLOTS",
        asyncio.Semaphore(learning_routes.PDF_LINK_VIDEO_CONCURRENCY),
    )
    monkeypatch.setattr(
        learning_routes,
        "_PDF_LINK_VIDEO_TRANSCODE_SLOT",
        asyncio.Semaphore(learning_routes.PDF_LINK_VIDEO_TRANSCODE_CONCURRENCY),
    )
    yield
    for tarefa in list(learning_routes._PDF_LINK_VIDEO_TASKS):
        tarefa.cancel()
    learning_routes._PDF_LINK_VIDEO_TASKS.clear()


def test_a_ancora_da_url_nao_vira_download_repetido() -> None:
    """Quatro botões para o mesmo vídeo, uma cópia guardada.

    O `#` de uma URL nunca chega ao servidor HTTP — é do navegador. Mas entrava
    na chave do derivado, e a chave é o registro do que já foi preparado. Um PDF
    citando `um.mov#1` até `um.mov#4` fazia a plataforma baixar quatro vezes o
    mesmo arquivo e guardar quatro cópias idênticas, com nomes diferentes.
    """
    alvos = [trusted_video_target(f"{ORIGEM}#{n}") for n in range(1, 5)]
    assert alvos == [ORIGEM] * 4
    chaves = {derivative_key(VENDEDOR, alvo) for alvo in alvos if alvo}
    assert len(chaves) == 1
    assert chaves == {derivative_key(VENDEDOR, trusted_video_target(ORIGEM) or "")}


def test_a_ancora_sai_mas_a_consulta_fica() -> None:
    # A `query` faz parte do endereço que o servidor recebe; a âncora não. Tirar
    # as duas transformaria endereços diferentes no mesmo arquivo.
    assert trusted_video_target(f"{ORIGEM}?token=abc#trecho") == f"{ORIGEM}?token=abc"


def test_endereco_fora_da_origem_permitida_continua_recusado() -> None:
    assert trusted_video_target("https://outro.example.test/v.mp4#1") is None


async def test_prepare_video_espera_o_portao_antes_de_converter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """O portão de conversão está no caminho real, e cobre só a conversão.

    Antes dele fica o download; depois, o hash do derivado e a subida para o
    storage. Nenhum dos dois fala com o serviço isolado, e segurar o portão
    durante eles faria a fila render menos do que pode.
    """
    portao = asyncio.Semaphore(1)
    comecou = asyncio.Event()

    async def _download(_target: str, destino: Path) -> tuple[int, str]:
        await to_thread.run_sync(partial(destino.write_bytes, b"origem"))
        return 6, "validador"

    def _remux(_source: Path, _size: int, _sha: str, destination: Path) -> bool:
        comecou.set()
        destination.write_bytes(b"derivado")
        return True

    monkeypatch.setattr(pdf_link_video, "download_source", _download)
    monkeypatch.setattr(pdf_link_video, "remux_in_sandbox", _remux)

    await portao.acquire()
    tarefa = asyncio.create_task(
        pdf_link_video.prepare_video(ORIGEM, tmp_path, transcode_gate=portao)
    )
    try:
        await asyncio.sleep(0.05)
        assert not comecou.is_set()
        portao.release()
        preparado = await asyncio.wait_for(tarefa, timeout=5)
        assert comecou.is_set()
        assert preparado.byte_size == len(b"derivado")
    finally:
        tarefa.cancel()


async def test_duas_conversoes_nunca_se_sobrepoem(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dois preparos de verdade, e as conversões em fila.

    O caminho é o real de ponta a ponta: `_store_pdf_link_video` chama
    `prepare_video`, que espera o portão antes de falar com o serviço isolado.
    Só o download e o próprio serviço são dublados.

    Um teste que segurasse o semáforo por fora provaria que semáforos funcionam
    e nada sobre este código usar o dele. Sem o portão, o que acontece em
    produção é o serviço isolado responder `429` ao segundo, que insiste quatro
    vezes, de três em três segundos, e desiste: o aluno fica sem vídeo.
    """
    tranca = threading.Lock()
    convertendo = 0
    maximo_convertendo = 0
    solta = threading.Event()

    async def _download(_target: str, destino: Path) -> tuple[int, str]:
        await to_thread.run_sync(partial(destino.write_bytes, b"origem"))
        return 6, "validador"

    def _remux(_source: Path, _size: int, _sha: str, destination: Path) -> bool:
        nonlocal convertendo, maximo_convertendo
        with tranca:
            convertendo += 1
            maximo_convertendo = max(maximo_convertendo, convertendo)
        try:
            solta.wait(timeout=10)
        finally:
            with tranca:
                convertendo -= 1
        destination.write_bytes(b"derivado")
        return True

    async def _ate(condicao: Any, prazo: float) -> bool:
        limite = time.monotonic() + prazo
        while time.monotonic() < limite:
            if condicao():
                return True
            await asyncio.sleep(0.01)
        return False

    monkeypatch.setattr(pdf_link_video, "download_source", _download)
    monkeypatch.setattr(pdf_link_video, "remux_in_sandbox", _remux)

    guardado: list[str] = []
    storage = cast(
        S3Storage,
        SimpleNamespace(put_verified=lambda **kwargs: guardado.append(kwargs["destination_key"])),
    )

    tarefas = [
        asyncio.create_task(
            learning_routes._store_pdf_link_video(storage, "appsell-ready", f"chave-{i}", ORIGEM)
        )
        for i in range(2)
    ]
    try:
        assert await _ate(lambda: maximo_convertendo >= 1, prazo=5)
        # Quanto mais se espera, mais chance a segunda teria de entrar se
        # pudesse. Ela não pode.
        assert not await _ate(lambda: maximo_convertendo >= 2, prazo=0.5)
    finally:
        solta.set()
        erros = await asyncio.gather(*tarefas, return_exceptions=True)
    assert [erro for erro in erros if isinstance(erro, BaseException)] == []
    assert sorted(guardado) == ["chave-0", "chave-1"]
    assert maximo_convertendo == 1


async def test_um_preparo_por_vez(monkeypatch: pytest.MonkeyPatch) -> None:
    """Um preparo vivo de cada vez, e não dois.

    Cada preparo mantém no disco a origem e o derivado até terminar o hash e a
    subida — até 1 GiB, com o teto de 512 MiB por arquivo. Dois seriam 2 GiB, e
    no ambiente de prévia o `/tmp` é `tmpfs`: esses bytes contam contra o
    `mem_limit` de 1 GiB. Em produção o `/tmp` é disco, mas a memória do serviço
    é menor ainda, 768 MiB.

    Dois dobrariam a vazão quando dois alunos clicam junto. O preço de errar é a
    API inteira cair por falta de memória, e por isso o número só sobe junto com
    os limites — #320.
    """
    vivos = 0
    maximo_vivo = 0
    entrou = asyncio.Event()
    solta = asyncio.Event()

    monkeypatch.setattr(learning_routes, "_pdf_link_video_ready", lambda *_: None)

    async def _preso(_storage: Any, _bucket: str, _key: str, _target: str) -> None:
        nonlocal vivos, maximo_vivo
        vivos += 1
        maximo_vivo = max(maximo_vivo, vivos)
        entrou.set()
        try:
            await solta.wait()
        finally:
            vivos -= 1

    monkeypatch.setattr(learning_routes, "_store_pdf_link_video", _preso)

    tarefas = [
        asyncio.create_task(
            learning_routes._run_pdf_link_video_preparation(
                cast(S3Storage, object()),
                "appsell-ready",
                f"chave-{indice}",
                ORIGEM,
                replace=False,
            )
        )
        for indice in range(4)
    ]
    try:
        await asyncio.wait_for(entrou.wait(), timeout=5)
        # Quanto mais se espera, mais chance os outros teriam de entrar se
        # pudessem.
        await asyncio.sleep(0.2)
        assert maximo_vivo == 1
    finally:
        solta.set()
        await asyncio.gather(*tarefas, return_exceptions=True)
    assert maximo_vivo == 1


def test_o_prazo_do_cliente_acompanha_o_do_ffmpeg() -> None:
    """Os dois prazos são constantes diferentes, e têm de carregar o mesmo valor.

    Baixar só o do cliente faria a API declarar falha enquanto o `ffmpeg`
    continua rodando — `subprocess.run` numa thread não é cancelado quando quem
    pediu desiste. O lugar do serviço isolado seguiria ocupado, e a leitura de
    links de todo mundo seguiria atrás dele, sem ninguém mais esperando o vídeo.
    """
    from appsell_api import video_remux

    assert pdf_link_video.TRANSCODE_TIMEOUT_SECONDS == video_remux.TRANSCODE_TIMEOUT_SECONDS
