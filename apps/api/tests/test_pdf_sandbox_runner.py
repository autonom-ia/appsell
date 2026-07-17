from __future__ import annotations

import os
import socket
import sys
import tempfile
from pathlib import Path

import pytest

from appsell_api.pdf_sandbox_runner import _assert_socket, _prepare_socket_path


def identity() -> tuple[int, int]:
    return os.geteuid(), os.getegid()


def test_prepare_accepts_empty_owned_private_directory(tmp_path: Path) -> None:
    os.chmod(tmp_path, 0o700)
    uid, gid = identity()

    _prepare_socket_path(tmp_path / "pdf.sock", expected_uid=uid, expected_gid=gid)


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS ignores chmod mode on Unix sockets")
def test_prepare_removes_only_a_valid_stale_socket() -> None:
    with tempfile.TemporaryDirectory(prefix="pdf-socket-", dir="/tmp") as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        uid, gid = identity()
        path = root / "pdf.sock"
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            stale.bind(str(path))
            os.chmod(path, 0o660)
        finally:
            stale.close()

        _assert_socket(path, expected_uid=uid, expected_gid=gid)
        _prepare_socket_path(path, expected_uid=uid, expected_gid=gid)

        assert not path.exists()


def test_prepare_rejects_unexpected_file(tmp_path: Path) -> None:
    os.chmod(tmp_path, 0o700)
    uid, gid = identity()
    (tmp_path / "tenant-content.pdf").write_bytes(b"private")

    with pytest.raises(RuntimeError, match="unexpected files"):
        _prepare_socket_path(tmp_path / "pdf.sock", expected_uid=uid, expected_gid=gid)


def test_prepare_rejects_permissive_directory(tmp_path: Path) -> None:
    os.chmod(tmp_path, 0o755)  # noqa: S103 - intentionally exercise fail-closed mode check.
    uid, gid = identity()

    with pytest.raises(RuntimeError, match="ownership or mode"):
        _prepare_socket_path(tmp_path / "pdf.sock", expected_uid=uid, expected_gid=gid)
