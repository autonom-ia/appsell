from __future__ import annotations

import os
import socket
from pathlib import Path

import uvicorn

SOCKET_PATH = Path("/run/appsell-pdf/pdf.sock")


def main() -> None:
    SOCKET_PATH.parent.mkdir(mode=0o770, parents=False, exist_ok=True)
    if SOCKET_PATH.exists() or SOCKET_PATH.is_symlink():
        if SOCKET_PATH.is_dir():
            raise RuntimeError("PDF sandbox socket path is a directory")
        SOCKET_PATH.unlink()

    server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server_socket.bind(str(SOCKET_PATH))
        os.chmod(SOCKET_PATH, 0o660)
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
        if SOCKET_PATH.exists() and not SOCKET_PATH.is_dir():
            SOCKET_PATH.unlink()


if __name__ == "__main__":
    main()
