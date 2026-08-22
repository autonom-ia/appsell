from __future__ import annotations

import asyncio
import signal

from anyio import to_thread
from fastapi import FastAPI

from .asset_worker_runner import run_asset_worker_loop
from .config import get_settings
from .db import create_engine, create_session_factory
from .internal_requests import InternalRequestAuthenticator
from .main import _pdf_sandbox_ready
from .storage_s3 import S3Storage
from .storage_worker import (
    ClamAvScanner,
    DeterministicLocalScanner,
    MalwareScanner,
    assert_storage_worker_ready,
)


async def run() -> None:
    settings = get_settings()
    if not settings.storage_configured or not settings.internal_auth_configured:
        raise RuntimeError("asset worker requires storage and internal authentication")

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    auth = InternalRequestAuthenticator.from_settings(settings, session_factory)
    storage = S3Storage(settings)
    scanner: MalwareScanner
    if settings.malware_scanner_provider == "clamav":
        scanner = ClamAvScanner(settings.malware_scanner_socket)
    elif settings.malware_scanner_provider == "deterministic" and settings.environment in {
        "local",
        "ci",
    }:
        scanner = DeterministicLocalScanner()
    else:
        raise RuntimeError("asset worker scanner is not configured")

    if not await _pdf_sandbox_ready(settings):
        raise RuntimeError("asset worker PDF parser is unavailable")
    await to_thread.run_sync(
        lambda: assert_storage_worker_ready(
            environment=settings.environment,
            storage=storage,
            scanner=scanner,
            allowed_origin=settings.storage_allowed_origin,
            cors_mode=settings.storage_cors_mode,
        )
    )

    worker_app = FastAPI()
    worker_app.state.settings = settings
    worker_app.state.internal_auth = auth
    worker_app.state.storage = storage
    worker_app.state.asset_scanner = scanner
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for handled_signal in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(handled_signal, stop.set)
    try:
        await run_asset_worker_loop(worker_app, stop, allow_remote=True)
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
