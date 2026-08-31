from __future__ import annotations

import hashlib
import io
import json
import shutil
import struct
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from appsell_api import video_verifier
from appsell_api.video_verifier import (
    VIDEO_MP4_MIME,
    VideoProbeUnavailable,
    VideoRejected,
    VideoSandboxUnavailable,
    prepared_catalogue_video,
    probe_mp4_file,
    spooled_video_file,
)


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", len(payload) + 8, kind) + payload


def minimal_mp4() -> bytes:
    return (
        box(b"ftyp", b"isom\0\0\0\0isommp42") + box(b"moov", b"metadata") + box(b"mdat", b"video")
    )


def test_spools_a_bounded_structural_mp4() -> None:
    content = minimal_mp4()
    with spooled_video_file(
        io.BytesIO(content),
        expected_size=len(content),
        expected_sha256_hex=hashlib.sha256(content).hexdigest(),
        declared_content_type=VIDEO_MP4_MIME,
    ) as verified:
        assert verified.path.read_bytes() == content


@pytest.mark.parametrize(
    "content",
    [
        b"not-an-mp4",
        box(b"ftyp", b"evil\0\0\0\0evil") + box(b"moov", b"x") + box(b"mdat", b"y"),
        box(b"ftyp", b"isom\0\0\0\0isom") + box(b"mdat", b"missing-moov"),
        struct.pack(">I4s", 9999, b"ftyp") + b"isom",
    ],
)
def test_rejects_invalid_or_incomplete_mp4(content: bytes) -> None:
    with pytest.raises(VideoRejected):
        with spooled_video_file(
            io.BytesIO(content),
            expected_size=len(content),
            expected_sha256_hex=hashlib.sha256(content).hexdigest(),
            declared_content_type=VIDEO_MP4_MIME,
        ):
            pass


def test_rejects_mime_size_and_hash_mismatch() -> None:
    content = minimal_mp4()
    digest = hashlib.sha256(content).hexdigest()
    for size, sha256, media_type in [
        (len(content) + 1, digest, VIDEO_MP4_MIME),
        (len(content), "0" * 64, VIDEO_MP4_MIME),
        (len(content), digest, "application/octet-stream"),
    ]:
        with pytest.raises(VideoRejected):
            with spooled_video_file(
                io.BytesIO(content),
                expected_size=size,
                expected_sha256_hex=sha256,
                declared_content_type=media_type,
            ):
                pass


def test_real_ffprobe_accepts_a_browser_safe_h264_mp4(
    tmp_path: Path,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg/ffprobe are required for the real codec test")
    output = tmp_path / "browser-safe.mp4"
    subprocess.run(  # noqa: S603 - test argv is locally controlled
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x180:d=0.2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-y",
            str(output),
        ],
        check=True,
        timeout=20,
    )
    probe_mp4_file(output, ffprobe)


@pytest.mark.parametrize(
    ("audio_streams", "error"),
    [
        ([{"codec_type": "audio", "codec_name": "opus"}], "must be AAC"),
        (
            [
                {"codec_type": "audio", "codec_name": "aac"},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
            "at most one audio",
        ),
    ],
)
def test_probe_rejects_non_aac_or_multiple_audio_streams(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    audio_streams: list[dict[str, str]],
    error: str,
) -> None:
    source = tmp_path / "candidate.mp4"
    source.write_bytes(b"x")
    payload = {
        "format": {"format_name": "mp4", "duration": "1.0", "size": "1"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 320,
                "height": 180,
                "pix_fmt": "yuv420p",
            },
            *audio_streams,
        ],
    }
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=json.dumps(payload).encode()),
    )
    with pytest.raises(VideoRejected, match=error):
        probe_mp4_file(source)


def test_probe_accepts_zero_or_one_aac_audio_stream(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "candidate.mp4"
    source.write_bytes(b"x")
    for audio_streams in ([], [{"codec_type": "audio", "codec_name": "aac"}]):
        payload = {
            "format": {"format_name": "mp4", "duration": "1.0", "size": "1"},
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 320,
                    "height": 180,
                    "pix_fmt": "yuv420p",
                },
                *audio_streams,
            ],
        }
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *_args, payload=payload, **_kwargs: SimpleNamespace(
                stdout=json.dumps(payload).encode()
            ),
        )
        probe_mp4_file(source)


def test_missing_ffprobe_is_infrastructure_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "candidate.mp4"
    source.write_bytes(b"x")
    monkeypatch.setattr(
        subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError())
    )

    with pytest.raises(VideoProbeUnavailable):
        probe_mp4_file(source)


def _sandbox(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, handler: Any) -> None:
    """O serviço isolado, encenado, sem deixar de exercitar o cliente real."""
    monkeypatch.setenv("APPSELL_API_PDF_SANDBOX_SOCKET", str(tmp_path / "pdf.sock"))
    monkeypatch.delenv("APPSELL_API_PDF_SANDBOX_URL", raising=False)
    monkeypatch.setattr(
        "appsell_api.video_verifier.httpx.HTTPTransport",
        lambda **_kwargs: httpx.MockTransport(handler),
    )
    monkeypatch.setattr("appsell_api.video_verifier.time.sleep", lambda _segundos: None)


def _origem(tmp_path: Path) -> Path:
    caminho = tmp_path / "origem.mp4"
    caminho.write_bytes(minimal_mp4())
    return caminho


def test_o_derivado_volta_do_sandbox_gravado_em_disco(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    preparado = b"MP4-COM-O-INDICE-NO-COMECO"
    pedidos: list[httpx.Request] = []

    def responder(request: httpx.Request) -> httpx.Response:
        pedidos.append(request)
        return httpx.Response(200, content=preparado, headers={"content-type": VIDEO_MP4_MIME})

    _sandbox(monkeypatch, tmp_path, responder)
    origem = _origem(tmp_path)
    with prepared_catalogue_video(origem) as resultado:
        assert resultado.path.read_bytes() == preparado
        assert resultado.byte_size == len(preparado)
        assert resultado.sha256_hex == hashlib.sha256(preparado).hexdigest()
        assert resultado.attempts == 1
        guardado = resultado.path

    # O modo é o que faz o sandbox recusar recodificar e conferir a saída.
    assert pedidos[0].headers["x-appsell-mode"] == "catalogue"
    assert pedidos[0].headers["x-appsell-sha256"] == hashlib.sha256(minimal_mp4()).hexdigest()
    assert pedidos[0].headers["x-appsell-byte-size"] == str(len(minimal_mp4()))
    assert not guardado.exists()


def test_o_sandbox_reciclando_e_esperado_e_a_chamada_se_repete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Conexão recusada é o isolamento funcionando, não defeito.

    O serviço mata o próprio processo depois de cada trabalho de mídia. Quem
    chega no meio do reinício leva recusa e só precisa esperar.
    """
    tentativas = 0

    def responder(_request: httpx.Request) -> httpx.Response:
        nonlocal tentativas
        tentativas += 1
        if tentativas < 3:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, content=b"MP4", headers={"content-type": VIDEO_MP4_MIME})

    _sandbox(monkeypatch, tmp_path, responder)
    with prepared_catalogue_video(_origem(tmp_path)) as resultado:
        assert resultado.attempts == 3


def test_uma_recusa_do_sandbox_nao_vira_nova_tentativa(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Repetir um vídeo que o sandbox reprovou daria no mesmo, quatro vezes."""
    tentativas = 0

    def responder(_request: httpx.Request) -> httpx.Response:
        nonlocal tentativas
        tentativas += 1
        return httpx.Response(422)

    _sandbox(monkeypatch, tmp_path, responder)
    with pytest.raises(VideoRejected), prepared_catalogue_video(_origem(tmp_path)):
        pass
    assert tentativas == 1


def test_o_sandbox_indisponivel_ate_o_fim_e_erro_de_infra(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def responder(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    _sandbox(monkeypatch, tmp_path, responder)
    with pytest.raises(VideoSandboxUnavailable), prepared_catalogue_video(_origem(tmp_path)):
        pass


def test_endereco_de_rede_nao_serve_para_preparar_video(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """O isolamento depende do socket: contêiner sem rede e sem credenciais."""
    monkeypatch.setenv("APPSELL_API_PDF_SANDBOX_SOCKET", str(tmp_path / "pdf.sock"))
    monkeypatch.setenv("APPSELL_API_PDF_SANDBOX_URL", "http://pdf-sandbox.interno")
    with pytest.raises(VideoSandboxUnavailable), prepared_catalogue_video(_origem(tmp_path)):
        pass


def test_o_prazo_do_cliente_acompanha_o_do_ffmpeg() -> None:
    """Desistir antes do `ffmpeg` deixaria o único lugar do sandbox ocupado.

    `subprocess.run` numa thread não é cancelado quando o cliente desiste: a API
    declararia falha e o trabalho continuaria rodando lá dentro, segurando o
    próximo vídeo de todo mundo.
    """
    from appsell_api.video_remux import REMUX_TIMEOUT_SECONDS

    assert video_verifier.PREPARE_TIMEOUT_SECONDS == REMUX_TIMEOUT_SECONDS


def test_o_prazo_curto_depende_de_o_catalogo_nunca_recodificar() -> None:
    """A amarração que faltava, e que nada guardava.

    Cinco minutos cobrem trocar o invólucro. Recodificar tem prazo de uma hora,
    doze vezes maior. Se o modo catálogo passar a aceitar recodificação sem este
    número subir junto, a API desiste aos cinco minutos e o `ffmpeg` continua
    rodando lá dentro — o único lugar do serviço isolado fica ocupado por um
    trabalho que ninguém mais espera, e todo mundo atrás dele leva 429.

    Este teste falha nos dois sentidos: se o modo catálogo deixar de recusar
    recodificação, e se os prazos deixarem de corresponder.
    """
    from appsell_api import pdf_sandbox_service
    from appsell_api.video_remux import REMUX_TIMEOUT_SECONDS, TRANSCODE_TIMEOUT_SECONDS

    assert TRANSCODE_TIMEOUT_SECONDS > REMUX_TIMEOUT_SECONDS
    assert video_verifier.PREPARE_TIMEOUT_SECONDS < TRANSCODE_TIMEOUT_SECONDS
    fonte = Path(pdf_sandbox_service.__file__).read_text(encoding="utf-8")
    assert "allow_transcode=not catalogue" in fonte, (
        "o modo catálogo deixou de recusar recodificação; ou volte a recusar, "
        "ou suba PREPARE_TIMEOUT_SECONDS para o prazo da recodificação"
    )
