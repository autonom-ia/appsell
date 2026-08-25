"""Verificação e saneamento da capa de um produto.

Uma capa é a única imagem que um vendedor manda e que outras pessoas veem. Ela
chega como bytes arbitrários, então três coisas precisam ser verdade antes de
ela ser guardada:

1. **O tipo vem da assinatura, não do nome nem do cabeçalho.** Um arquivo que se
   declara PNG e começa com ``<svg`` é um documento executável no navegador, não
   uma imagem. SVG é recusado explicitamente por isso.
2. **A imagem é reescrita, não repassada.** É assim que o EXIF some — junto com
   qualquer bloco auxiliar que o formato permita carregar. O que fica guardado é
   a reescrita, então quem chama precisa registrar o digest **dela**, e não o do
   arquivo enviado. Uma imagem que já chegou limpa pode sair byte a byte igual;
   o ponto não é o digest mudar, é ele descrever o que está guardado.
3. **As dimensões são limitadas antes de decodificar.** Um PNG de poucos
   quilobytes pode declarar dezenas de milhares de pixels por lado e estourar a
   memória do processo ao ser aberto.

Os tipos de capa vivem aqui e **não** em ``asset_media.SUPPORTED_MEDIA`` de
propósito: alargar aquele catálogo permitiria anexar uma capa como material de
aula, que é outro caminho, com outra política de download.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Final

from PIL import Image, UnidentifiedImageError

_CHUNK: Final = 64 * 1024
_MIB: Final = 1024 * 1024

COVER_MAX_BYTES: Final = 5 * _MIB
COVER_MAX_SIDE: Final = 4_000
COVER_MIN_SIDE: Final = 64
COVER_MAX_PIXELS: Final = 12_000_000

JPEG_MEDIA_TYPE: Final = "image/jpeg"
PNG_MEDIA_TYPE: Final = "image/png"
WEBP_MEDIA_TYPE: Final = "image/webp"

COVER_MEDIA_TYPES: Final = frozenset({JPEG_MEDIA_TYPE, PNG_MEDIA_TYPE, WEBP_MEDIA_TYPE})

# Assinatura por formato. O que o arquivo diz ser precisa bater com o que ele é.
_SIGNATURES: Final[dict[str, tuple[bytes, ...]]] = {
    JPEG_MEDIA_TYPE: (b"\xff\xd8\xff",),
    PNG_MEDIA_TYPE: (b"\x89PNG\r\n\x1a\n",),
    WEBP_MEDIA_TYPE: (b"RIFF",),
}

_PILLOW_FORMAT: Final[dict[str, str]] = {
    JPEG_MEDIA_TYPE: "JPEG",
    PNG_MEDIA_TYPE: "PNG",
    WEBP_MEDIA_TYPE: "WEBP",
}

# Começos que denunciam um documento se passando por imagem. SVG é o caso que a
# issue nomeia, mas qualquer XML ou HTML aqui é a mesma classe de problema.
_MARKUP_PREFIXES: Final = (b"<?xml", b"<svg", b"<!doctype", b"<html", b"<!--")


class ImageRejected(ValueError):
    """A capa não pode ser guardada como está."""


@dataclass(frozen=True)
class SanitisedCover:
    path: Path
    media_type: str
    byte_size: int
    sha256_hex: str
    width: int
    height: int


def is_cover_media(media_type: str) -> bool:
    return media_type in COVER_MEDIA_TYPES


@contextmanager
def spooled_cover_file(
    stream: IO[bytes],
    *,
    expected_size: int,
    expected_sha256_hex: str,
    declared_content_type: str,
) -> Iterator[SanitisedCover]:
    """Recebe a capa, verifica, e entrega uma versão reescrita sem metadados.

    O digest e o tamanho no resultado são os do arquivo **saneado**. O digest
    declarado pelo cliente é conferido contra o original, para o transporte ser
    verificado, mas não é o que fica gravado.
    """
    if not is_cover_media(declared_content_type):
        raise ImageRejected("declared media type is not a supported cover image")
    if expected_size < 1 or expected_size > COVER_MAX_BYTES:
        raise ImageRejected("cover size is outside the allowed range")
    if len(expected_sha256_hex) != 64 or any(
        character not in "0123456789abcdef" for character in expected_sha256_hex
    ):
        raise ImageRejected("cover digest is malformed")

    handle, raw_path = tempfile.mkstemp(prefix="appsell-cover-src-")
    source = Path(raw_path)
    sanitised: Path | None = None
    try:
        digest = hashlib.sha256()
        written = 0
        with os.fdopen(handle, "wb") as sink:
            os.chmod(source, 0o600)
            while True:
                chunk = stream.read(_CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > COVER_MAX_BYTES:
                    raise ImageRejected("cover exceeded the allowed size while reading")
                digest.update(chunk)
                sink.write(chunk)
            sink.flush()
            os.fsync(sink.fileno())
        if written != expected_size:
            raise ImageRejected("cover size differs from the upload intent")
        if digest.hexdigest() != expected_sha256_hex:
            raise ImageRejected("cover digest differs from the upload intent")

        _reject_markup(source)
        _require_signature(source, declared_content_type)
        sanitised = _rewrite_without_metadata(source, declared_content_type)
        yield _describe(sanitised, declared_content_type)
    finally:
        source.unlink(missing_ok=True)
        if sanitised is not None:
            sanitised.unlink(missing_ok=True)


def _head(path: Path, size: int = 64) -> bytes:
    with path.open("rb") as handle:
        return handle.read(size)


def _reject_markup(path: Path) -> None:
    head = _head(path).lstrip().lower()
    for prefix in _MARKUP_PREFIXES:
        if head.startswith(prefix):
            raise ImageRejected("cover looks like markup, not an image")


def _require_signature(path: Path, media_type: str) -> None:
    head = _head(path, 16)
    if not any(head.startswith(signature) for signature in _SIGNATURES[media_type]):
        raise ImageRejected("cover bytes do not match the declared image format")
    # RIFF sozinho é contêiner: só é WebP se disser WEBP no lugar certo.
    if media_type == WEBP_MEDIA_TYPE and head[8:12] != b"WEBP":
        raise ImageRejected("cover declares WebP but the container says otherwise")


def _rewrite_without_metadata(source: Path, media_type: str) -> Path:
    handle, raw_path = tempfile.mkstemp(prefix="appsell-cover-")
    os.close(handle)
    target = Path(raw_path)
    os.chmod(target, 0o600)
    try:
        with Image.open(source) as opened:
            if (opened.format or "") != _PILLOW_FORMAT[media_type]:
                raise ImageRejected("cover format does not match the declared media type")
            width, height = opened.size
            _require_dimensions(width, height)
            opened.load()
            mode = "RGB" if media_type == JPEG_MEDIA_TYPE else opened.mode
            if mode not in {"RGB", "RGBA", "L"}:
                mode = "RGBA" if "A" in opened.mode else "RGB"
            converted = opened.convert(mode)
            # Reconstruir a imagem a partir só dos bytes de pixel é o que deixa
            # EXIF, ICC, XMP e blocos auxiliares para trás: nada do original
            # acompanha, porque nada além dos pixels é copiado.
            clean = Image.frombytes(mode, converted.size, converted.tobytes())
            clean.save(target, format=_PILLOW_FORMAT[media_type], optimize=False)
    except ImageRejected:
        target.unlink(missing_ok=True)
        raise
    except (UnidentifiedImageError, OSError, ValueError) as error:
        target.unlink(missing_ok=True)
        raise ImageRejected("cover could not be decoded as an image") from error
    return target


def _require_dimensions(width: int, height: int) -> None:
    if width < COVER_MIN_SIDE or height < COVER_MIN_SIDE:
        raise ImageRejected("cover is smaller than the minimum side")
    if width > COVER_MAX_SIDE or height > COVER_MAX_SIDE:
        raise ImageRejected("cover is larger than the maximum side")
    if width * height > COVER_MAX_PIXELS:
        raise ImageRejected("cover declares more pixels than allowed")


def _describe(path: Path, media_type: str) -> SanitisedCover:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    with Image.open(path) as opened:
        width, height = opened.size
    return SanitisedCover(
        path=path,
        media_type=media_type,
        byte_size=path.stat().st_size,
        sha256_hex=digest.hexdigest(),
        width=width,
        height=height,
    )
