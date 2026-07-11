from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Response, status
from sqlalchemy.ext.asyncio import AsyncEngine

from .config import get_settings
from .db import create_engine, database_ready


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    settings = get_settings()
    engine = create_engine(settings)
    app.state.db_engine = engine
    yield
    await engine.dispose()


app = FastAPI(
    title="Autonom.ia Sell Domain API",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


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
