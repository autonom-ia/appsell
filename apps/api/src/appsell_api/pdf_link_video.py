"""O vídeo do PDF toca no navegador, qualquer que seja o container de origem.

## O que estava errado

Os vídeos que o vendedor referencia de dentro do PDF são `.mov` gravados no
QuickTime. O proxy os repassava com `content-type: video/quicktime` e
`nosniff`, e o Chrome recusa esse tipo: `canPlayType("video/quicktime")` devolve
string vazia. O aluno via "Não foi possível reproduzir este vídeo" e não tinha o
que fazer — o formato do arquivo não é problema dele.

Medido em 30/08/2026 no arquivo real do vendedor: o container é QuickTime
(`ftyp` brand `qt  `), mas **o conteúdo já é H.264 Main + AAC**, exatamente o
que todo navegador toca. Só o invólucro estava errado.

## O que este módulo faz

Prepara um derivado MP4 e o guarda no storage, uma vez por endereço de origem:

- codecs já apresentáveis → `-c copy`, troca só o container. Medido em 4 s para
  um arquivo de 5 MB, sem perda e sem recodificar.
- codecs que o navegador não toca (ProRes, HEVC, AV1 em container antigo) →
  recodifica para H.264/AAC. É caro, e por isso acontece em segundo plano.

O `+faststart` põe o índice no começo do arquivo, que é o que permite ao aluno
arrastar a barra sem baixar o vídeo inteiro.

## Onde ele NÃO confia

O endereço vem de dentro de um PDF que o vendedor enviou, e quem busca passa a
ser o servidor. Isso é uma requisição saindo da nossa rede a mando de um dado
de terceiro, então o alvo é conferido contra a mesma allowlist de origens que o
BFF já usa (`APPSELL_PDF_VIDEO_ALLOWED_ORIGINS`) antes de qualquer download.
Sem allowlist configurada, nada é buscado.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

_MIB: Final = 1024 * 1024

#: Teto do arquivo de origem. Acima disto o preparo é recusado em vez de ocupar
#: a máquina por tempo indefinido com um arquivo que ninguém dimensionou.
MAX_SOURCE_BYTES: Final = 512 * _MIB
#: Teto do derivado gerado, pelo mesmo motivo.
MAX_DERIVATIVE_BYTES: Final = 512 * _MIB
#: Duração máxima aceita, em segundos. Uma aula não é um filme.
MAX_DURATION_SECONDS: Final = 4 * 60 * 60
#: Orçamento de tempo do `ffmpeg`. Recodificar é lento, mas não é infinito.
REMUX_TIMEOUT_SECONDS: Final = 5 * 60
TRANSCODE_TIMEOUT_SECONDS: Final = 60 * 60

#: O que o navegador toca dentro de um MP4, sem recodificar.
_COPYABLE_VIDEO: Final = frozenset({"h264"})
_COPYABLE_AUDIO: Final = frozenset({"aac", "mp3"})

DERIVATIVE_MEDIA_TYPE: Final = "video/mp4"
DERIVATIVE_PREFIX: Final = "derivatives/pdf-link-video"


class PdfLinkVideoRejected(RuntimeError):
    """A origem não pode virar um vídeo apresentável, e não adianta tentar de novo."""


class PdfLinkVideoUnavailable(RuntimeError):
    """O preparo falhou por uma condição passageira e pode ser repetido."""


@dataclass(frozen=True)
class PreparedVideo:
    path: Path
    byte_size: int
    sha256_hex: str
    recoded: bool


def allowed_video_origins() -> tuple[str, ...]:
    """As origens de onde um vídeo do PDF pode ser buscado.

    Espelha `APPSELL_PDF_VIDEO_ALLOWED_ORIGINS`, a mesma variável que o BFF já
    consulta. Duas listas independentes divergiriam, e a que decide agora é
    esta: é o servidor que faz a requisição.
    """
    raw = os.environ.get("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", "")
    return tuple(origin.strip() for origin in raw.split(",") if origin.strip())


def trusted_video_target(value: object) -> str | None:
    """O endereço, conferido antes de o servidor buscar qualquer coisa."""
    if not isinstance(value, str) or len(value) > 2_048:
        return None
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme != "https" or parts.username or parts.password or not parts.hostname:
        return None
    if port is not None and port != 443:
        return None
    origins = allowed_video_origins()
    if not origins:
        return None
    origin = f"https://{parts.hostname}"
    return value if origin in origins else None


def derivative_key(target_url: str) -> str:
    """A chave do derivado, derivada do endereço de origem.

    A existência do objeto é o registro: sem tabela, sem migration, e sem um
    segundo lugar para o estado divergir do storage.
    """
    digest = hashlib.sha256(target_url.encode("utf-8")).hexdigest()
    return f"{DERIVATIVE_PREFIX}/{digest[:2]}/{digest}.mp4"


def _binary(name: str) -> str:
    """O caminho absoluto da ferramenta, resolvido uma vez.

    Chamar por nome deixaria a resolução a cargo do `PATH` do processo, que não
    é nosso para garantir. E a ausência vira uma falha nomeada em vez de um
    `OSError` genérico lá adiante.
    """
    resolved = shutil.which(name)
    if resolved is None:
        raise PdfLinkVideoUnavailable(f"{name} is not available in this image")
    return resolved


def probe_video(target_url: str) -> dict[str, Any]:
    """Lê container, duração e codecs sem baixar o arquivo inteiro."""
    try:
        # noqa S603: argv fixo, sem shell; o único dado externo é `target_url`,
        # e ele já passou por `trusted_video_target` — https, porta 443 e
        # origem na allowlist.
        completed = subprocess.run(  # noqa: S603 - argv only; target checked against the allowlist
            [
                _binary("ffprobe"), "-v", "error", "-show_streams", "-show_format",
                "-of", "json", "-i", target_url,
            ],
            capture_output=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise PdfLinkVideoUnavailable("video probe exceeded its budget") from error
    except OSError as error:
        raise PdfLinkVideoUnavailable("video probe could not start") from error
    if completed.returncode != 0:
        raise PdfLinkVideoRejected("source is not a readable video")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise PdfLinkVideoRejected("video probe returned no usable metadata") from error


def copyable_streams(probed: dict[str, Any]) -> bool:
    """`True` quando basta trocar o container; `False` quando precisa recodificar."""
    fmt = probed.get("format") or {}
    try:
        duration = float(fmt.get("duration") or 0.0)
        size = int(fmt.get("size") or 0)
    except (TypeError, ValueError) as error:
        raise PdfLinkVideoRejected("video metadata is not numeric") from error
    if duration <= 0 or duration > MAX_DURATION_SECONDS:
        raise PdfLinkVideoRejected("video duration is outside the accepted range")
    if size > MAX_SOURCE_BYTES:
        raise PdfLinkVideoRejected("video exceeds the accepted source size")

    streams = probed.get("streams") or []
    video = [s for s in streams if s.get("codec_type") == "video"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if not video:
        raise PdfLinkVideoRejected("source carries no video stream")
    # Uma faixa de cada, no máximo: o player mostra uma, e copiar as demais só
    # engorda o derivado.
    if len(video) > 1 or len(audio) > 1:
        return False
    if str(video[0].get("codec_name")) not in _COPYABLE_VIDEO:
        return False
    if audio and str(audio[0].get("codec_name")) not in _COPYABLE_AUDIO:
        return False
    return True


def _run_ffmpeg(arguments: list[str], *, timeout: int) -> None:
    try:
        completed = subprocess.run(  # noqa: S603 - argv only; target checked against the allowlist
            [_binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", *arguments],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise PdfLinkVideoUnavailable("video preparation exceeded its budget") from error
    except OSError as error:
        raise PdfLinkVideoUnavailable("video preparation could not start") from error
    if completed.returncode != 0:
        raise PdfLinkVideoRejected("video preparation failed")


def prepare_video(target_url: str, workspace: Path) -> PreparedVideo:
    """Baixa a origem e devolve um MP4 que o navegador toca."""
    trusted = trusted_video_target(target_url)
    if trusted is None:
        raise PdfLinkVideoRejected("video target is not an allowed origin")

    copyable = copyable_streams(probe_video(trusted))
    destination = workspace / "derivative.mp4"
    common = ["-i", trusted, "-map", "0:v:0", "-map", "0:a:0?",
              "-movflags", "+faststart", "-f", "mp4"]
    if copyable:
        arguments = [*common, "-c", "copy", str(destination)]
        timeout = REMUX_TIMEOUT_SECONDS
    else:
        arguments = [
            *common,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            # Alguns codecs antigos trazem dimensões ímpares, que o H.264 recusa.
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:a", "aac", "-b:a", "128k", "-ac", "2",
            str(destination),
        ]
        timeout = TRANSCODE_TIMEOUT_SECONDS
    _run_ffmpeg(arguments, timeout=timeout)

    if not destination.exists():
        raise PdfLinkVideoRejected("video preparation produced no file")
    byte_size = destination.stat().st_size
    if byte_size < 1 or byte_size > MAX_DERIVATIVE_BYTES:
        raise PdfLinkVideoRejected("prepared video is outside the accepted size")
    digest = hashlib.sha256()
    with destination.open("rb") as prepared:
        for chunk in iter(lambda: prepared.read(_MIB), b""):
            digest.update(chunk)
    return PreparedVideo(
        path=destination,
        byte_size=byte_size,
        sha256_hex=digest.hexdigest(),
        recoded=not copyable,
    )


class PreparationRegistry:
    """Quem já está preparando o quê, para não preparar duas vezes.

    Sem isto, cada acesso do aluno enquanto o vídeo não fica pronto dispara um
    `ffmpeg` novo — e o caso em que o preparo é lento é exatamente o caso em que
    o aluno insiste.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: set[str] = set()

    def start(self, key: str) -> bool:
        with self._lock:
            if key in self._running:
                return False
            self._running.add(key)
            return True

    def finish(self, key: str) -> None:
        with self._lock:
            self._running.discard(key)

    def running(self, key: str) -> bool:
        with self._lock:
            return key in self._running


def temporary_workspace() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(prefix="appsell-pdf-video-")
