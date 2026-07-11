from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Response, status
from sqlalchemy.ext.asyncio import AsyncEngine

from .catalog_routes import router as catalog_router
from .config import get_settings
from .db import create_engine, create_session_factory, database_ready
from .internal_requests import InternalRequestAuthenticator
from .routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    settings = get_settings()
    engine = create_engine(settings)
    app.state.db_engine = engine
    session_factory = create_session_factory(engine)
    app.state.db_session_factory = session_factory
    app.state.internal_auth = (
        InternalRequestAuthenticator.from_settings(settings, session_factory)
        if settings.internal_auth_configured
        else None
    )
    yield
    await engine.dispose()


app = FastAPI(
    title="Autonom.ia Sell Domain API",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)
app.include_router(router)
app.include_router(catalog_router)


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
async def ready(response: Response) -> dict[str, Any]:
    engine: AsyncEngine = app.state.db_engine
    ready_now = await database_ready(engine)
    if not ready_now:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if ready_now else "unavailable", "database": ready_now}
