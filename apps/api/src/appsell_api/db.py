from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import Settings

API_POOL_SIZE = 5
API_POOL_MAX_OVERFLOW = 0


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=API_POOL_SIZE,
        max_overflow=API_POOL_MAX_OVERFLOW,
        pool_timeout=3,
        connect_args={
            "timeout": 3,
            "command_timeout": 3,
            "server_settings": {"application_name": settings.database_application_name},
        },
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with factory() as session, session.begin():
        yield session


async def database_ready(engine: AsyncEngine) -> bool:
    try:
        async with engine.connect() as connection:
            value = await connection.scalar(
                text("select app_private.schema_ready()"),
                execution_options={"timeout": 3},
            )
            return bool(value)
    except Exception:  # readiness deliberately collapses driver details
        return False


async def financial_channel_ready(engine: AsyncEngine) -> bool | None:
    """O canal Financial resolve (#465)? `None` quando não deu para perguntar.

    Banco fora ou schema atrás já reprovam por `database_ready`; aqui o
    desconhecido não vira "canal quebrado".
    """
    try:
        async with engine.connect() as connection:
            value = await connection.scalar(
                text("select app_private.financial_channel_ready()"),
                execution_options={"timeout": 3},
            )
            return bool(value)
    except Exception:  # readiness deliberately collapses driver details
        return None
