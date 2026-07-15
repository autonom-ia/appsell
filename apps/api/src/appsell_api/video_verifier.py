from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import httpx

MAX_VIDEO_BYTES = 100 * 1024 * 1024
VIDEO_MP4_MIME = "video/mp4"
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


def probe_video_safely(path: Path) -> None:
    remote_url = os.environ.get("APPSELL_API_PDF_SANDBOX_URL", "").strip()
    remote_socket = os.environ.get("APPSELL_API_PDF_SANDBOX_SOCKET", "").strip()
    if remote_url or not remote_socket:
        raise VideoSandboxUnavailable("video probe requires an isolated Unix socket")
    try:
        endpoint = "http://pdf-sandbox/probe-video"
        byte_size = path.stat().st_size
        if not 1 <= byte_size <= MAX_VIDEO_BYTES:
            raise VideoRejected("video size is outside the allowed range")
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(_CHUNK_BYTES):
                digest.update(chunk)
        headers = {
            "content-type": VIDEO_MP4_MIME,
            "x-appsell-byte-size": str(byte_size),
            "x-appsell-sha256": digest.hexdigest(),
        }
        transport = httpx.HTTPTransport(uds=remote_socket, retries=0)
        with (
            path.open("rb") as payload,
            httpx.Client(
                transport=transport,
                timeout=httpx.Timeout(20.0, connect=3.0),
                trust_env=False,
                follow_redirects=False,
            ) as client,
        ):
            response = client.post(endpoint, content=payload, headers=headers)
    except (VideoRejected, VideoSandboxUnavailable):
        raise
    except (httpx.HTTPError, OSError, ValueError) as error:
        raise VideoSandboxUnavailable("video probe isolation service is unavailable") from error
    if response.status_code in {413, 415, 422}:
        raise VideoRejected("video probe isolation service rejected the video")
    if response.status_code != 204 or response.content:
        raise VideoSandboxUnavailable("video probe isolation service returned an invalid response")
