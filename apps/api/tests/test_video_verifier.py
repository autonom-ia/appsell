from __future__ import annotations

import hashlib
import io
import json
import shutil
import struct
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from appsell_api.video_verifier import (
    VIDEO_MP4_MIME,
    VideoProbeUnavailable,
    VideoRejected,
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
