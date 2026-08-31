"""O vídeo do vendedor vira um MP4 que o navegador toca.

Este módulo executa `ffprobe` e `ffmpeg` sobre um arquivo que o vendedor
controla, e por isso **só o serviço isolado o importa**. Rodar um parser de
mídia dentro da API significaria dar a um arquivo de terceiro o mesmo processo
que carrega as credenciais do banco e as chaves internas — o projeto já trata
mídia não confiável assim desde `video_verifier`, e
`test_parser_de_midia_fica_no_sandbox.py` impede que a regra se perca de novo.

## O que ele faz

O container de origem costuma ser QuickTime, que o Chrome recusa: o `.mov` do
vendedor traz `ftyp` brand `qt  `, e `canPlayType("video/quicktime")` devolve
string vazia. Mas o conteúdo dentro dele já é H.264 Main + AAC — o que todo
navegador toca. Só o invólucro estava errado.

- codecs apresentáveis → `-c copy`, troca só o container. Medido em 4 s para um
  arquivo de 5 MB, sem perda e sem recodificar.
- codecs que o navegador não desenha → recodifica para H.264/AAC.

`+faststart` põe o índice no começo, que é o que deixa o aluno arrastar a barra
sem baixar o vídeo inteiro.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any, Final

_MIB: Final = 1024 * 1024

#: Teto do MP4 que sai daqui. O mesmo do catálogo de mídia.
#:
#: Eram 512 MiB, número que nasceu neste módulo sem olhar o catálogo — onde
#: `video/mp4` sempre valeu 100 MiB. O mesmo conteúdo tinha dois tetos, cinco
#: vezes distantes, dependendo de ter sido enviado pelo vendedor ou citado
#: dentro do PDF.
MAX_DERIVATIVE_BYTES: Final = 100 * _MIB
#: Duração máxima aceita, em segundos.
#:
#: Vinte minutos, e o número desceu de quatro horas de propósito. Quando o
#: arquivo precisa ser recodificado, a conversão é **mais lenta que o próprio
#: vídeo**: medido no serviço isolado, 66 segundos para cada minuto de 1080p,
#: com as duas threads que ele tem. Quatro horas levariam quatro horas e meia, e
#: o prazo da conversão é de uma — o vídeo era aceito no envio e nunca ficava
#: pronto, com o aluno esperando por algo que não ia chegar.
#:
#: Vinte minutos convertem em vinte e dois, dentro do prazo, e recusar no envio
#: é melhor do que aceitar e falhar depois.
#:
#: Nada disso vale para quem manda MP4/H.264: aí o container só troca de nome, a
#: 0,15 s por minuto de vídeo, e este teto nunca é o que atrapalha.
MAX_DURATION_SECONDS: Final = 20 * 60
#: Acima disto o decodificador aloca quadros enormes antes de qualquer redução.
MAX_SOURCE_PIXELS: Final = 7680 * 4320
MAX_SOURCE_FPS: Final = 120
#: Teto da saída do `ffprobe`, lida para a memória.
MAX_PROBE_BYTES: Final = 256 * 1024

REMUX_TIMEOUT_SECONDS: Final = 5 * 60
TRANSCODE_TIMEOUT_SECONDS: Final = 60 * 60

#: O que o navegador toca dentro de um MP4, sem recodificar.
_COPYABLE_VIDEO: Final = frozenset({"h264"})
_COPYABLE_AUDIO: Final = frozenset({"aac", "mp3"})
#: 10 bits e 4:2:2 são H.264 e mesmo assim o navegador não desenha.
_COPYABLE_PIXEL_FORMATS: Final = frozenset({"yuv420p", "yuvj420p"})

#: Os containers que aceitamos abrir.
#:
#: `hls`, `dash` e `concat` ficam de fora de propósito: são formatos que apontam
#: para outros recursos, e é por eles que um parser de mídia vira um cliente de
#: rede. A restrição vale para a inspeção e para a conversão.
ALLOWED_FORMATS: Final = "mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,avi,mpegts,mpeg,asf,flv,ogg"


class VideoRemuxRejected(RuntimeError):
    """A origem não pode virar um vídeo apresentável, e repetir daria no mesmo."""


class VideoRemuxUnavailable(RuntimeError):
    """Falhou por uma condição passageira e pode ser repetido."""


def _binary(name: str) -> str:
    resolved = shutil.which(name)
    if resolved is None:
        raise VideoRemuxUnavailable(f"{name} is not available in this image")
    return resolved


def probe_video(source: Path) -> dict[str, Any]:
    """Container, duração, codecs e dimensões do arquivo local."""
    arguments = [
        _binary("ffprobe"),
        "-v",
        "error",
        "-protocol_whitelist",
        "file",
        "-format_whitelist",
        ALLOWED_FORMATS,
        "-show_entries",
        "format=duration:stream=codec_type,codec_name,pix_fmt,width,height,r_frame_rate",
        "-of",
        "json",
        "-i",
        str(source),
    ]
    try:
        # `capture_output` leria a saída inteira para a memória e só depois
        # deixaria conferir o tamanho — o teto chegaria tarde demais para servir
        # de teto.
        with subprocess.Popen(  # noqa: S603 - argv only; local path, no shell
            arguments, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        ) as process:
            output = process.stdout.read(MAX_PROBE_BYTES + 1) if process.stdout else b""
            try:
                code = process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                process.kill()
                raise
    except subprocess.TimeoutExpired as error:
        raise VideoRemuxUnavailable("video probe exceeded its budget") from error
    except OSError as error:
        raise VideoRemuxUnavailable("video probe could not start") from error
    if code != 0:
        raise VideoRemuxRejected("source is not a readable video")
    if len(output) > MAX_PROBE_BYTES:
        raise VideoRemuxRejected("video metadata is larger than expected")
    try:
        return dict(json.loads(output))
    except json.JSONDecodeError as error:
        raise VideoRemuxRejected("video probe returned no usable metadata") from error


def _frame_rate(value: object) -> float:
    """`r_frame_rate` chega como fração, no formato `30000/1001`."""
    try:
        numerator, _, denominator = str(value).partition("/")
        divisor = float(denominator or 1)
        # `0/0` chega de arquivos sem taxa declarada; devolver zero faz quem
        # chama recusar, que é o que se quer.
        return float(numerator) / divisor if divisor else 0.0
    except (TypeError, ValueError):
        return 0.0


def copyable_streams(probed: dict[str, Any]) -> bool:
    """`True` quando basta trocar o container; `False` quando precisa recodificar."""
    fmt = probed.get("format") or {}
    try:
        duration = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError) as error:
        raise VideoRemuxRejected("video metadata is not numeric") from error
    # `NaN` falha toda comparação, então passaria por qualquer faixa sem ser
    # visto. Exigir número finito é o que fecha isso.
    if not math.isfinite(duration) or duration <= 0 or duration > MAX_DURATION_SECONDS:
        raise VideoRemuxRejected("video duration is outside the accepted range")

    streams = probed.get("streams") or []
    video = [s for s in streams if s.get("codec_type") == "video"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if not video:
        raise VideoRemuxRejected("source carries no video stream")

    # A redução para 1080p acontece depois de decodificar. Sem um teto aqui, um
    # quadro absurdo é alocado inteiro antes de qualquer redução — o limite tem
    # de valer sobre a origem, não sobre a saída.
    width = int(video[0].get("width") or 0)
    height = int(video[0].get("height") or 0)
    if width < 1 or height < 1 or width * height > MAX_SOURCE_PIXELS:
        raise VideoRemuxRejected("video dimensions are outside the accepted range")
    frame_rate = _frame_rate(video[0].get("r_frame_rate"))
    if not math.isfinite(frame_rate) or frame_rate <= 0 or frame_rate > MAX_SOURCE_FPS:
        raise VideoRemuxRejected("video frame rate is outside the accepted range")

    # Uma faixa de cada, no máximo: o player mostra uma, e copiar as demais só
    # engorda o derivado.
    if len(video) > 1 or len(audio) > 1:
        return False
    if str(video[0].get("codec_name")) not in _COPYABLE_VIDEO:
        return False
    if str(video[0].get("pix_fmt")) not in _COPYABLE_PIXEL_FORMATS:
        return False
    if audio and str(audio[0].get("codec_name")) not in _COPYABLE_AUDIO:
        return False
    return True


def remux_to_mp4(source: Path, destination: Path) -> bool:
    """Grava o MP4 apresentável. Devolve `True` quando precisou recodificar."""
    copyable = copyable_streams(probe_video(source))
    common = [
        # O `ffmpeg` não fala com a rede em momento nenhum.
        "-protocol_whitelist",
        "file",
        "-format_whitelist",
        ALLOWED_FORMATS,
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-movflags",
        "+faststart",
        # Teto de bytes na própria escrita, e não só depois que o arquivo existe.
        "-fs",
        str(MAX_DERIVATIVE_BYTES),
        "-threads",
        "2",
        "-f",
        "mp4",
    ]
    if copyable:
        arguments = [*common, "-c", "copy", str(destination)]
        timeout = REMUX_TIMEOUT_SECONDS
    else:
        arguments = [
            *common,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-vf",
            "scale='min(1920,trunc(iw/2)*2)':'min(1080,trunc(ih/2)*2)'"
            ":force_original_aspect_ratio=decrease",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ac",
            "2",
            str(destination),
        ]
        timeout = TRANSCODE_TIMEOUT_SECONDS
    try:
        completed = subprocess.run(  # noqa: S603 - argv only; local path, no shell
            [_binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", *arguments],
            # Nada da saída é lido, e uma hora de erros prolixos acumulados na
            # memória seria um jeito barato de derrubar o serviço.
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise VideoRemuxUnavailable("video preparation exceeded its budget") from error
    except OSError as error:
        raise VideoRemuxUnavailable("video preparation could not start") from error
    if completed.returncode != 0:
        raise VideoRemuxRejected("video preparation failed")
    if not destination.exists():
        raise VideoRemuxRejected("video preparation produced no file")
    size = destination.stat().st_size
    if size < 1 or size > MAX_DERIVATIVE_BYTES:
        raise VideoRemuxRejected("prepared video is outside the accepted size")
    return not copyable
