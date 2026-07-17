from __future__ import annotations

import os
import socket
import stat
from pathlib import Path

import uvicorn

SOCKET_PATH = Path("/run/appsell-pdf/pdf.sock")


def main() -> None:
    if os.geteuid() != 10001 or os.getegid() != 10001:
        raise RuntimeError("PDF sandbox must run as the dedicated 10001:10001 user")
    _prepare_socket_path(SOCKET_PATH, expected_uid=10001, expected_gid=10001)
    os.umask(0o007)

    server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server_socket.bind(str(SOCKET_PATH))
        os.chmod(SOCKET_PATH, 0o660)
        _assert_socket(SOCKET_PATH, expected_uid=10001, expected_gid=10001)
        server_socket.listen(8)
        config = uvicorn.Config(
            "appsell_api.pdf_sandbox_service:app",
            workers=1,
            limit_concurrency=8,
            backlog=8,
            proxy_headers=False,
            forwarded_allow_ips="",
        )
        uvicorn.Server(config).run(sockets=[server_socket])
    finally:
        server_socket.close()
        if SOCKET_PATH.exists():
            _assert_socket(SOCKET_PATH, expected_uid=10001, expected_gid=10001)
            SOCKET_PATH.unlink()


def _prepare_socket_path(path: Path, *, expected_uid: int, expected_gid: int) -> None:
    parent = path.parent
    parent_stat = os.lstat(parent)
    if (
        not stat.S_ISDIR(parent_stat.st_mode)
        or stat.S_IMODE(parent_stat.st_mode) != 0o700
        or parent_stat.st_uid != expected_uid
        or parent_stat.st_gid != expected_gid
    ):
        raise RuntimeError("PDF sandbox socket directory ownership or mode is invalid")
    entries = list(parent.iterdir())
    if not entries:
        return
    if len(entries) != 1 or entries[0].name != path.name:
        raise RuntimeError("PDF sandbox socket directory contains unexpected files")
    _assert_socket(path, expected_uid=expected_uid, expected_gid=expected_gid)
    path.unlink()


def _assert_socket(path: Path, *, expected_uid: int, expected_gid: int) -> None:
    socket_stat = os.lstat(path)
    if (
        not stat.S_ISSOCK(socket_stat.st_mode)
        or stat.S_IMODE(socket_stat.st_mode) != 0o660
        or socket_stat.st_uid != expected_uid
        or socket_stat.st_gid != expected_gid
    ):
        raise RuntimeError("PDF sandbox socket ownership, type or mode is invalid")


if __name__ == "__main__":
    main()
