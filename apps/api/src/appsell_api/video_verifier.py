from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Final

import httpx

from .asset_media import VIDEO_MEDIA_TYPE, max_bytes

#: O teto do material de aula vem do catálogo, que é quem o banco e a tela
#: também consultam. Escrito à mão aqui, ele sobrevivia a quem pensasse tê-lo
#: mudado.
MAX_VIDEO_BYTES = max_bytes(VIDEO_MEDIA_TYPE)

#: O que um trabalho de vídeo segura em disco ao mesmo tempo.
#:
#: A origem, baixada da quarentena, e o derivado que volta do serviço isolado. O
#: mesmo par existe dos dois lados do socket: o worker segura os dois enquanto
#: escaneia e promove, e o serviço isolado segura os dois enquanto converte.
#:
#: `/tmp` é `tmpfs` nos dois, e `tmpfs` conta contra o cgroup: declarar disco
#: que não cabe na memória é prometer espaço que não existe.
MAX_VIDEO_JOB_TEMP_BYTES = 2 * MAX_VIDEO_BYTES
VIDEO_MP4_MIME = VIDEO_MEDIA_TYPE

#: Quantas vezes insistir quando o serviço isolado não atende.
#:
#: Ele se recicla depois de cada trabalho de mídia: é assim que um arquivo ruim
#: não contamina o próximo aluno. Enquanto reinicia, quem chega leva conexão
#: recusada, e isso não é falha, é o isolamento funcionando. Sem insistir,
#: preparar vários vídeos seguidos falhava um sim, um não.
SANDBOX_ATTEMPTS: Final = 4
SANDBOX_RETRY_SECONDS: Final = 3.0

#: Prazo do cliente para o preparo no serviço isolado.
#:
#: Carrega o mesmo valor de `video_remux.REMUX_TIMEOUT_SECONDS`, que é o prazo
#: do próprio `ffmpeg`, e não pode ser a mesma constante: `video_remux` executa
#: binário de mídia e não pode ser importado de dentro da API
#: (`test_parser_de_midia_fica_no_sandbox.py`). Desistir antes do `ffmpeg` faria
#: a API declarar falha enquanto o trabalho continua, ocupando o único lugar do
#: sandbox sem ninguém mais esperando por ele.
PREPARE_TIMEOUT_SECONDS: Final = 5 * 60
_CHUNK_BYTES = 1024 * 1024
_MAX_TOP_LEVEL_BOXES = 10_000
_ALLOWED_BRANDS = frozenset(
    {b"avc1", b"dash", b"iso2", b"iso5", b"iso6", b"isom", b"M4V ", b"mp41", b"mp42"}
)


class VideoRejected(ValueError):
    pass


class VideoSandboxUnavailable(RuntimeError):
    pass


class VideoProbeUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class PreparedCatalogueVideo:
    """O derivado que vai para o aluno, e quantas tentativas custou obtê-lo."""

    path: Path
    byte_size: int
    sha256_hex: str
    attempts: int


@dataclass(frozen=True)
class SpooledVideo:
    path: Path
    byte_size: int
    sha256_hex: str


@contextmanager
def spooled_video_file(
    source: BinaryIO,
    *,
    expected_size: int,
    expected_sha256_hex: str,
    declared_content_type: str,
    temporary_directory: str | None = None,
) -> Iterator[SpooledVideo]:
    if declared_content_type != VIDEO_MP4_MIME:
        raise VideoRejected("declared MIME is not video/mp4")
    if expected_size < 1 or expected_size > MAX_VIDEO_BYTES:
        raise VideoRejected("declared video size is outside the allowed range")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256_hex):
        raise VideoRejected("expected SHA-256 is invalid")

    path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix="appsell-video-",
            suffix=".mp4",
            dir=temporary_directory,
            delete=False,
        ) as destination:
            path = Path(destination.name)
            os.chmod(path, 0o600)
            digest = hashlib.sha256()
            byte_size = 0
            while chunk := source.read(_CHUNK_BYTES):
                byte_size += len(chunk)
                if byte_size > expected_size or byte_size > MAX_VIDEO_BYTES:
                    raise VideoRejected("video exceeds its declared or maximum size")
                digest.update(chunk)
                destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())

        if byte_size != expected_size:
            raise VideoRejected("video size does not match declaration")
        if digest.hexdigest() != expected_sha256_hex:
            raise VideoRejected("video SHA-256 does not match declaration")
        _validate_mp4_container(path, byte_size)
        yield SpooledVideo(path=path, byte_size=byte_size, sha256_hex=digest.hexdigest())
    finally:
        if path is not None:
            path.unlink(missing_ok=True)


def _validate_mp4_container(path: Path, byte_size: int) -> None:
    offset = 0
    box_count = 0
    box_types: list[bytes] = []
    with path.open("rb") as source:
        while offset < byte_size:
            box_count += 1
            if box_count > _MAX_TOP_LEVEL_BOXES:
                raise VideoRejected("video contains too many top-level boxes")
            source.seek(offset)
            header = source.read(8)
            if len(header) != 8:
                raise VideoRejected("video box header is truncated")
            size32, box_type = struct.unpack(">I4s", header)
            header_size = 8
            if size32 == 1:
                extended = source.read(8)
                if len(extended) != 8:
                    raise VideoRejected("video extended box size is truncated")
                box_size = struct.unpack(">Q", extended)[0]
                header_size = 16
            elif size32 == 0:
                box_size = byte_size - offset
            else:
                box_size = size32
            if box_size < header_size or box_size > byte_size - offset:
                raise VideoRejected("video box size is invalid")
            if not all(32 <= character <= 126 for character in box_type):
                raise VideoRejected("video box type is invalid")
            if box_count == 1:
                if box_type != b"ftyp" or box_size < 16 or box_size > 1024 * 1024:
                    raise VideoRejected("video does not start with a bounded ftyp box")
                payload = source.read(box_size - header_size)
                brands = [payload[index : index + 4] for index in range(0, len(payload), 4)]
                if (
                    len(payload) % 4
                    or not brands
                    or not any(brand in _ALLOWED_BRANDS for brand in brands)
                ):
                    raise VideoRejected("video brands are unsupported")
            box_types.append(box_type)
            offset += box_size

    if offset != byte_size or b"moov" not in box_types or b"mdat" not in box_types:
        raise VideoRejected("video requires complete moov and mdat boxes")


def probe_mp4_file(path: Path, binary: str = "ffprobe", timeout_seconds: float = 10.0) -> None:
    try:
        completed = subprocess.run(  # noqa: S603 - argv only; shell is never invoked
            [
                binary,
                "-v",
                "error",
                "-show_entries",
                "format=format_name,duration,size:stream=codec_type,codec_name,width,height,pix_fmt",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
        if len(completed.stdout) > 64 * 1024:
            raise VideoRejected("ffprobe response is too large")
        payload = json.loads(completed.stdout)
    except FileNotFoundError as error:
        raise VideoProbeUnavailable("video probe binary is unavailable") from error
    except (
        subprocess.TimeoutExpired,
        subprocess.CalledProcessError,
        json.JSONDecodeError,
    ) as error:
        raise VideoRejected("video probe failed closed") from error

    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("format"), dict)
        or not isinstance(payload.get("streams"), list)
    ):
        raise VideoRejected("video probe response is invalid")
    format_data = payload["format"]
    try:
        duration = float(format_data["duration"])
        reported_size = int(format_data["size"])
    except (KeyError, TypeError, ValueError) as error:
        raise VideoRejected("video duration or size is invalid") from error
    if (
        "mp4" not in str(format_data.get("format_name", "")).split(",")
        or not 0 < duration <= 6 * 60 * 60
    ):
        raise VideoRejected("video format or duration is unsupported")
    if reported_size != path.stat().st_size:
        raise VideoRejected("video probe size differs from verified bytes")

    video_streams = [
        stream
        for stream in payload["streams"]
        if isinstance(stream, dict) and stream.get("codec_type") == "video"
    ]
    audio_streams = [
        stream
        for stream in payload["streams"]
        if isinstance(stream, dict) and stream.get("codec_type") == "audio"
    ]
    other_streams = [
        stream
        for stream in payload["streams"]
        if not isinstance(stream, dict) or stream.get("codec_type") not in {"video", "audio"}
    ]
    if len(video_streams) != 1 or len(audio_streams) > 1 or other_streams:
        raise VideoRejected(
            "video must contain exactly one video stream and at most one audio stream"
        )
    if audio_streams and audio_streams[0].get("codec_name") != "aac":
        raise VideoRejected("video audio codec must be AAC")
    stream = video_streams[0]
    if stream.get("codec_name") != "h264" or stream.get("pix_fmt") not in {"yuv420p", "yuvj420p"}:
        raise VideoRejected("video codec is not browser-safe H.264 4:2:0")
    try:
        width = int(stream["width"])
        height = int(stream["height"])
    except (KeyError, TypeError, ValueError) as error:
        raise VideoRejected("video dimensions are invalid") from error
    if not 16 <= width <= 3840 or not 16 <= height <= 2160:
        raise VideoRejected("video dimensions are outside the allowed range")


def _sandbox_socket() -> str:
    """O socket do serviço isolado, e a recusa de qualquer outro transporte.

    Endereço de rede não serve: o isolamento depende de o parser rodar num
    contêiner sem rede e sem as credenciais da API.
    """
    remote_url = os.environ.get("APPSELL_API_PDF_SANDBOX_URL", "").strip()
    remote_socket = os.environ.get("APPSELL_API_PDF_SANDBOX_SOCKET", "").strip()
    if remote_url or not remote_socket:
        raise VideoSandboxUnavailable("video preparation requires an isolated Unix socket")
    return remote_socket


@contextmanager
def prepared_catalogue_video(source: Path) -> Iterator[PreparedCatalogueVideo]:
    """O MP4 que o aluno recebe: mesmo conteúdo, índice do container no começo.

    O `+faststart` é o ponto. Sem ele o índice do MP4 fica no fim do arquivo, o
    navegador precisa buscá-lo antes de desenhar o primeiro quadro, e o aluno
    não consegue arrastar a barra de progresso. Editores de vídeo exportam sem
    faststart por padrão, então isso valia para praticamente toda aula enviada.

    A conversão acontece no serviço isolado, nunca aqui: o arquivo é do
    vendedor, e um parser de mídia não tem o que fazer no processo que carrega
    as credenciais do banco.

    Conhecido e aceito: o derivado sai com tamanho praticamente igual ao da
    origem, mas mover o índice pode acrescentar alguns bytes. Um envio exatamente
    no teto pode passar no envio e ser recusado aqui. É uma recusa clara, e não
    um erro de banco no `INSERT`.
    """
    remote_socket = _sandbox_socket()
    byte_size = source.stat().st_size
    if not 1 <= byte_size <= MAX_VIDEO_BYTES:
        raise VideoRejected("video size is outside the allowed range")
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        while chunk := handle.read(_CHUNK_BYTES):
            digest.update(chunk)

    destination: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b", prefix="appsell-prepared-", suffix=".mp4", delete=False
        ) as sink:
            destination = Path(sink.name)
        os.chmod(destination, 0o600)
        for attempt in range(1, SANDBOX_ATTEMPTS + 1):
            try:
                prepared_size, prepared_sha = _prepare_once(
                    source, byte_size, digest.hexdigest(), destination, remote_socket
                )
            except VideoSandboxUnavailable:
                if attempt == SANDBOX_ATTEMPTS:
                    raise
                time.sleep(SANDBOX_RETRY_SECONDS)
                continue
            yield PreparedCatalogueVideo(
                path=destination,
                byte_size=prepared_size,
                sha256_hex=prepared_sha,
                attempts=attempt,
            )
            return
        raise VideoSandboxUnavailable("video preparation service did not answer")
    finally:
        if destination is not None:
            destination.unlink(missing_ok=True)


def _prepare_once(
    source: Path,
    byte_size: int,
    sha256_hex: str,
    destination: Path,
    remote_socket: str,
) -> tuple[int, str]:
    """Uma ida ao serviço isolado, com o derivado gravado em disco."""
    headers = {
        "content-type": "application/octet-stream",
        "x-appsell-byte-size": str(byte_size),
        "x-appsell-sha256": sha256_hex,
        "x-appsell-mode": "catalogue",
    }
    transport = httpx.HTTPTransport(uds=remote_socket, retries=0)
    written = 0
    digest = hashlib.sha256()
    try:
        with (
            httpx.Client(
                transport=transport,
                timeout=httpx.Timeout(PREPARE_TIMEOUT_SECONDS, connect=5.0),
                trust_env=False,
                follow_redirects=False,
            ) as client,
            source.open("rb") as payload,
            client.stream(
                "POST", "http://pdf-sandbox/prepare-video", content=payload, headers=headers
            ) as response,
        ):
            if response.status_code in {413, 415, 422}:
                raise VideoRejected("video preparation service rejected the video")
            if (
                response.status_code != 200
                or response.headers.get("content-type") != VIDEO_MP4_MIME
            ):
                raise VideoSandboxUnavailable("video preparation service is unavailable")
            # O derivado vai direto para o disco: cem megabytes na memória do
            # sandbox e de novo na da API derrubaria os dois.
            with destination.open("wb") as sink:
                for chunk in response.iter_bytes(_CHUNK_BYTES):
                    written += len(chunk)
                    if written > MAX_VIDEO_BYTES:
                        raise VideoRejected("prepared video is outside the allowed range")
                    digest.update(chunk)
                    sink.write(chunk)
    except (VideoRejected, VideoSandboxUnavailable):
        raise
    except (httpx.HTTPError, OSError, ValueError) as error:
        raise VideoSandboxUnavailable("video preparation service request failed") from error
    if written < 1:
        raise VideoRejected("video preparation service returned an empty body")
    return written, digest.hexdigest()
