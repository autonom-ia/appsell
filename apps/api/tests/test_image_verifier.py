"""A4.3 — a capa é aceita pela assinatura e guardada sem metadados."""

from __future__ import annotations

import hashlib
import io
from contextlib import AbstractContextManager

import pytest
from PIL import Image

from appsell_api.image_verifier import (
    COVER_MAX_BYTES,
    ImageRejected,
    SanitisedCover,
    is_cover_media,
    spooled_cover_file,
)


def encode(image: Image.Image, fmt: str, **options: object) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **options)
    return buffer.getvalue()


def solid(width: int = 320, height: int = 180, mode: str = "RGB") -> Image.Image:
    return Image.new(mode, (width, height), (12, 90, 160) if mode == "RGB" else 128)


def accept(payload: bytes, media_type: str) -> AbstractContextManager[SanitisedCover]:
    return spooled_cover_file(
        io.BytesIO(payload),
        expected_size=len(payload),
        expected_sha256_hex=hashlib.sha256(payload).hexdigest(),
        declared_content_type=media_type,
    )


def test_accepts_the_three_declared_formats() -> None:
    for fmt, media_type in (("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")):
        payload = encode(solid(), fmt)
        with accept(payload, media_type) as cover:
            assert cover.media_type == media_type
            assert cover.width == 320
            assert cover.height == 180
            assert cover.byte_size > 0
            assert len(cover.sha256_hex) == 64


def test_strips_exif_from_a_jpeg_that_carries_it() -> None:
    exif = Image.Exif()
    # 0x010F é Make; 0x9286 é UserComment. Nenhum dos dois pode sobreviver.
    exif[0x010F] = "Camera secreta"
    exif[0x9286] = "localizacao sensivel"
    payload = encode(solid(), "JPEG", exif=exif)
    assert b"Camera secreta" in payload, "o teste precisa começar com o EXIF presente"

    with accept(payload, "image/jpeg") as cover:
        stored = cover.path.read_bytes()

    assert b"Camera secreta" not in stored
    assert b"localizacao sensivel" not in stored
    with Image.open(io.BytesIO(stored)) as reopened:
        assert dict(reopened.getexif()) == {}


def test_strips_png_text_chunks() -> None:
    from PIL import PngImagePlugin

    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", "metadado que deve sumir")
    payload = encode(solid(), "PNG", pnginfo=info)
    assert b"metadado que deve sumir" in payload

    with accept(payload, "image/png") as cover:
        assert b"metadado que deve sumir" not in cover.path.read_bytes()


def test_the_stored_digest_describes_the_stored_file() -> None:
    # O invariante é este, e vale sempre: gravar o digest do arquivo enviado
    # faria o banco descrever algo que não é o que está guardado. Uma imagem
    # já limpa pode sair byte a byte igual à que entrou, e isso está certo.
    payload = encode(solid(), "PNG")

    with accept(payload, "image/png") as cover:
        assert cover.sha256_hex == hashlib.sha256(cover.path.read_bytes()).hexdigest()
        assert cover.byte_size == len(cover.path.read_bytes())


def test_the_digest_changes_when_there_was_metadata_to_remove() -> None:
    exif = Image.Exif()
    exif[0x010F] = "Camera secreta"
    payload = encode(solid(), "JPEG", exif=exif)
    original = hashlib.sha256(payload).hexdigest()

    with accept(payload, "image/jpeg") as cover:
        assert cover.sha256_hex != original
        assert cover.sha256_hex == hashlib.sha256(cover.path.read_bytes()).hexdigest()


def test_refuses_svg_even_when_it_claims_to_be_png() -> None:
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'

    with pytest.raises(ImageRejected, match="markup"):
        with accept(svg, "image/png"):
            pass


@pytest.mark.parametrize(
    "payload",
    [
        b'<?xml version="1.0"?><svg/>',
        b"<!DOCTYPE html><html><body>oi</body></html>",
        b"<html><script>alert(1)</script></html>",
        b"   \n\t<svg/>",
    ],
)
def test_refuses_any_markup_disguised_as_an_image(payload: bytes) -> None:
    with pytest.raises(ImageRejected, match="markup"):
        with accept(payload, "image/png"):
            pass


def test_refuses_bytes_that_do_not_match_the_declared_format() -> None:
    png = encode(solid(), "PNG")

    with pytest.raises(ImageRejected, match="do not match"):
        with accept(png, "image/jpeg"):
            pass


def test_refuses_a_riff_container_that_is_not_webp() -> None:
    fake = b"RIFF" + (0).to_bytes(4, "little") + b"AVI " + b"\x00" * 64

    with pytest.raises(ImageRejected, match="container"):
        with accept(fake, "image/webp"):
            pass


def test_refuses_a_media_type_outside_the_cover_set() -> None:
    assert not is_cover_media("image/gif")
    assert not is_cover_media("image/svg+xml")
    assert not is_cover_media("application/pdf")

    with pytest.raises(ImageRejected, match="not a supported cover"):
        with accept(encode(solid(), "PNG"), "image/gif"):
            pass


def test_refuses_dimensions_outside_the_bounds() -> None:
    with pytest.raises(ImageRejected, match="minimum side"):
        with accept(encode(solid(32, 32), "PNG"), "image/png"):
            pass

    with pytest.raises(ImageRejected, match="maximum side"):
        with accept(encode(solid(4_200, 100), "PNG"), "image/png"):
            pass


def test_refuses_a_size_or_digest_that_disagrees_with_the_intent() -> None:
    payload = encode(solid(), "PNG")

    with pytest.raises(ImageRejected, match="size differs"):
        with spooled_cover_file(
            io.BytesIO(payload),
            expected_size=len(payload) + 1,
            expected_sha256_hex=hashlib.sha256(payload).hexdigest(),
            declared_content_type="image/png",
        ):
            pass

    with pytest.raises(ImageRejected, match="digest differs"):
        with spooled_cover_file(
            io.BytesIO(payload),
            expected_size=len(payload),
            expected_sha256_hex="0" * 64,
            declared_content_type="image/png",
        ):
            pass


def test_refuses_a_declared_size_above_the_ceiling() -> None:
    payload = encode(solid(), "PNG")

    with pytest.raises(ImageRejected, match="outside the allowed range"):
        with spooled_cover_file(
            io.BytesIO(payload),
            expected_size=COVER_MAX_BYTES + 1,
            expected_sha256_hex=hashlib.sha256(payload).hexdigest(),
            declared_content_type="image/png",
        ):
            pass


def test_removes_both_temporary_files_on_the_way_out() -> None:
    payload = encode(solid(), "PNG")

    with accept(payload, "image/png") as cover:
        path = cover.path
        assert path.exists()

    assert not path.exists()
