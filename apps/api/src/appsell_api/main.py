import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
from anyio import to_thread
from fastapi import FastAPI, Response, status
from sqlalchemy.ext.asyncio import AsyncEngine

from .asset_routes import router as asset_router
from .asset_worker_runner import run_asset_worker_loop
from .catalog_routes import router as catalog_router
from .checkout_routes import router as checkout_router
from .config import Settings, get_settings
from .cover_routes import router as cover_router
from .db import create_engine, create_session_factory, database_ready
from .demo_checkout_worker import run_demo_checkout_worker_loop
from .email_worker import run_email_worker_loop
from .fulfillment_worker import run_fulfillment_worker_loop
from .internal_requests import InternalRequestAuthenticator
from .learning_routes import router as learning_router
from .logging_setup import configure_logging, install_request_logging
from .payout_routes import router as payout_router
from .payout_sync_routes import router as payout_sync_router
from .platform_bootstrap_routes import router as platform_bootstrap_router
from .platform_routes import router as platform_router
from .public_routes import router as public_router
from .routes import router
from .storage_s3 import S3Storage
from .storage_worker import (
    ClamAvScanner,
    DeterministicLocalScanner,
    MalwareScanner,
    assert_storage_worker_ready,
)
from .webhook_routes import router as webhook_router
from .webhook_secrets import WebhookSecretResolver


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    settings = get_settings()
    # O pacote ja configurou o log lendo o ambiente cru no import, para que uma
    # falha aqui dentro tambem saia formatada. Esta chamada refina com os
    # `Settings` validados, que sao a fonte de verdade e leem tambem o `.env`.
    configure_logging(environment=settings.environment, log_level=settings.log_level)
    app.state.settings = settings
    engine = create_engine(settings)
    app.state.db_engine = engine
    session_factory = create_session_factory(engine)
    app.state.db_session_factory = session_factory
    app.state.internal_auth = (
        InternalRequestAuthenticator.from_settings(settings, session_factory)
        if settings.internal_auth_configured
        else None
    )
    app.state.webhook_secrets = WebhookSecretResolver.from_settings(settings)
    app.state.storage = None
    app.state.asset_scanner = None
    app.state.inline_asset_worker_enabled = False
    app.state.storage_runtime_ready = settings.environment in {"local", "ci"}
    app.state.storage_controls_ready = settings.environment in {"local", "ci"}
    app.state.pdf_sandbox_runtime_ready = not settings.pdf_sandbox_required
    app.state.storage_checked_at = time.monotonic()
    app.state.storage_controls_checked_at = 0.0
    app.state.pdf_sandbox_checked_at = time.monotonic()
    if settings.pdf_sandbox_required:
        # Sem parar de subir por causa disto. A API atende muita coisa que não
        # passa pelo serviço isolado, e não há ordem garantida entre os dois no
        # arranque: morrer aqui trocaria "o leitor de PDF está fora" por "a API
        # inteira está fora", e mais um laço de reinício até o outro aparecer.
        #
        # Quem responde pela verdade é o `ready`, que devolve 503 enquanto o
        # serviço não atende, e o monitor periódico, que recupera sozinho quando
        # ele volta. Quem precisa mesmo do sandbox para existir é o worker, e é
        # ele quem recusa subir sem — decisão dele, no boot dele.
        app.state.pdf_sandbox_runtime_ready = await _pdf_sandbox_ready(settings)
        app.state.pdf_sandbox_checked_at = time.monotonic()
    if settings.storage_configured:
        storage = S3Storage(settings)
        scanner: MalwareScanner
        if settings.malware_scanner_provider == "deterministic":
            scanner = DeterministicLocalScanner()
        elif settings.malware_scanner_provider == "clamav":
            scanner = ClamAvScanner(settings.malware_scanner_socket)
        else:
            raise RuntimeError("malware scanner adapter is not configured")
        if settings.environment in {"local", "ci"}:
            await to_thread.run_sync(
                lambda: storage.ensure_local_ci_controls(
                    allowed_origin=settings.storage_allowed_origin
                )
            )
        await to_thread.run_sync(
            lambda: assert_storage_worker_ready(
                environment=settings.environment,
                storage=storage,
                scanner=scanner,
                allowed_origin=settings.storage_allowed_origin,
                cors_mode=getattr(settings, "storage_cors_mode", "s3"),
            )
        )
        app.state.storage = storage
        app.state.asset_scanner = scanner
        app.state.storage_runtime_ready = True
        app.state.storage_controls_ready = True
        # O sandbox não entra aqui. Ter storage e ter parser de PDF são coisas
        # diferentes, e esta linha desfazia a sonda de alguns parágrafos acima:
        # o serviço isolado podia estar fora e a API respondia pronta até a
        # primeira passagem do monitor.
        app.state.storage_checked_at = time.monotonic()
        app.state.storage_controls_checked_at = time.monotonic()
        app.state.pdf_sandbox_checked_at = time.monotonic()
        app.state.inline_asset_worker_enabled = settings.environment in {"local", "ci"}
    dependency_stop = asyncio.Event()
    dependency_task: asyncio.Task[None] | None = None
    if settings.storage_configured or settings.pdf_sandbox_required:
        dependency_task = asyncio.create_task(_monitor_storage_dependencies(app, dependency_stop))
    worker_stop = asyncio.Event()
    worker_task: asyncio.Task[None] | None = None
    if app.state.inline_asset_worker_enabled and app.state.internal_auth is not None:
        worker_task = asyncio.create_task(run_asset_worker_loop(app, worker_stop))
    checkout_worker_stop = asyncio.Event()
    checkout_worker_task: asyncio.Task[None] | None = None
    if settings.environment in {"local", "ci"} and app.state.internal_auth is not None:
        checkout_worker_task = asyncio.create_task(
            run_demo_checkout_worker_loop(app, checkout_worker_stop)
        )
    fulfillment_worker_stop = asyncio.Event()
    fulfillment_worker_task: asyncio.Task[None] | None = None
    if app.state.internal_auth is not None:
        fulfillment_worker_task = asyncio.create_task(
            run_fulfillment_worker_loop(app, fulfillment_worker_stop)
        )
    # O worker de e-mail decide sozinho se inicia: sem SMTP configurado ele
    # registra e sai, e as linhas da caixa de saída ficam pendentes.
    email_worker_stop = asyncio.Event()
    email_worker_task: asyncio.Task[None] | None = None
    if app.state.internal_auth is not None:
        email_worker_task = asyncio.create_task(run_email_worker_loop(app, email_worker_stop))
    try:
        yield
    finally:
        email_worker_stop.set()
        if email_worker_task is not None:
            # Um envio em curso é uma thread de SMTP com até três timeouts de
            # 20 s; cancelar a task não fecha o socket. Espera o suficiente
            # para o envio terminar e ser registrado, senão sai duplicado.
            try:
                await asyncio.wait_for(email_worker_task, timeout=65)
            except TimeoutError:
                email_worker_task.cancel()
        dependency_stop.set()
        if dependency_task is not None:
            try:
                await asyncio.wait_for(dependency_task, timeout=5)
            except TimeoutError:
                dependency_task.cancel()
        worker_stop.set()
        if worker_task is not None:
            try:
                await asyncio.wait_for(worker_task, timeout=5)
            except TimeoutError:
                worker_task.cancel()
        checkout_worker_stop.set()
        if checkout_worker_task is not None:
            try:
                await asyncio.wait_for(checkout_worker_task, timeout=5)
            except TimeoutError:
                checkout_worker_task.cancel()
        fulfillment_worker_stop.set()
        if fulfillment_worker_task is not None:
            try:
                await asyncio.wait_for(fulfillment_worker_task, timeout=5)
            except TimeoutError:
                fulfillment_worker_task.cancel()
        await engine.dispose()


app = FastAPI(
    title="Autonom.ia Sell Domain API",
    version="0.1.0",
    openapi_url=None,
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)
install_request_logging(app)
app.include_router(router)
app.include_router(catalog_router)
app.include_router(checkout_router)
app.include_router(payout_router)
app.include_router(payout_sync_router)
app.include_router(learning_router)
app.include_router(asset_router)
app.include_router(cover_router)
app.include_router(platform_bootstrap_router)
app.include_router(platform_router)
app.include_router(public_router)
app.include_router(webhook_router)


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
async def ready(response: Response) -> dict[str, Any]:
    engine: AsyncEngine = app.state.db_engine
    database = await database_ready(engine)
    storage = await _storage_ready()
    settings = get_settings()
    pdf_sandbox = _cached_dependency_ready(
        "pdf_sandbox_runtime_ready",
        "pdf_sandbox_checked_at",
        settings,
        required=settings.pdf_sandbox_required,
    )
    ready_now = database and storage and pdf_sandbox
    if not ready_now:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ok" if ready_now else "unavailable",
        "database": database,
        "storage": storage,
        "pdf_sandbox": pdf_sandbox,
    }


async def _storage_ready() -> bool:
    settings = get_settings()
    if not settings.storage_configured:
        return settings.environment in {"local", "ci"}
    return _cached_dependency_ready(
        "storage_runtime_ready", "storage_checked_at", settings
    ) and bool(getattr(app.state, "storage_controls_ready", False))


def _cached_dependency_ready(
    attribute: str,
    checked_at_attribute: str,
    settings: Settings,
    *,
    required: bool = True,
) -> bool:
    if not required:
        return True
    checked_at = float(getattr(app.state, checked_at_attribute, 0.0))
    return time.monotonic() - checked_at <= settings.storage_readiness_stale_after_seconds and bool(
        getattr(app.state, attribute, False)
    )


async def _monitor_storage_dependencies(app_instance: FastAPI, stop: asyncio.Event) -> None:
    while not stop.is_set():
        await _refresh_storage_dependencies(app_instance)
        try:
            await asyncio.wait_for(
                stop.wait(),
                timeout=app_instance.state.settings.storage_health_probe_interval_seconds,
            )
        except TimeoutError:
            continue


async def _refresh_storage_dependencies(
    app_instance: FastAPI, *, force_control_audit: bool = False
) -> None:
    """Refresh cheap health on every tick and expensive controls on cadence.

    A failed control audit remains sticky and is retried on the next health tick,
    so readiness becomes 503 on drift and recovers only after a successful audit.
    """
    settings: Settings = app_instance.state.settings
    storage: S3Storage | None = app_instance.state.storage
    scanner = app_instance.state.asset_scanner
    now = time.monotonic()
    if settings.storage_configured:
        operational = False
        try:
            if storage is None or scanner is None:
                raise RuntimeError("storage dependencies are unavailable")
            await to_thread.run_sync(storage.probe_availability)
            operational = await to_thread.run_sync(scanner.healthy)
        except Exception:
            operational = False

        controls_ready = bool(getattr(app_instance.state, "storage_controls_ready", False))
        last_control_audit = float(getattr(app_instance.state, "storage_controls_checked_at", 0.0))
        audit_due = (
            force_control_audit
            or not controls_ready
            or now - last_control_audit >= settings.storage_control_audit_interval_seconds
        )
        if storage is not None and audit_due:
            try:
                await to_thread.run_sync(
                    lambda: storage.assert_runtime_controls(
                        allowed_origin=settings.storage_allowed_origin,
                        require_tls=settings.environment in {"staging", "production"},
                        cors_mode=getattr(settings, "storage_cors_mode", "s3"),
                    )
                )
                controls_ready = True
            except Exception:
                controls_ready = False
            app_instance.state.storage_controls_checked_at = now

        app_instance.state.storage_controls_ready = controls_ready
        app_instance.state.storage_runtime_ready = operational and controls_ready
        app_instance.state.storage_checked_at = now

    if settings.pdf_sandbox_required:
        try:
            parser_ready = await _pdf_sandbox_ready(settings)
        except Exception:
            parser_ready = False
        app_instance.state.pdf_sandbox_runtime_ready = parser_ready
        app_instance.state.pdf_sandbox_checked_at = now


async def _pdf_sandbox_ready(settings: Settings) -> bool:
    if not settings.pdf_sandbox_required:
        return True
    health_url = (
        "http://pdf-sandbox/health"
        if settings.pdf_sandbox_socket
        else settings.pdf_sandbox_url.rsplit("/", 1)[0] + "/health"
    )
    try:
        transport = (
            httpx.AsyncHTTPTransport(uds=settings.pdf_sandbox_socket)
            if settings.pdf_sandbox_socket
            else None
        )
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(2.0, connect=1.0),
            transport=transport,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            response = await client.get(health_url, headers={"accept": "application/json"})
        return response.status_code == 200 and response.json() == {"status": "ok"}
    except (httpx.HTTPError, ValueError):
        return False
