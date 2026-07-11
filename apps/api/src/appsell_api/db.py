from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_timeout=3,
        connect_args={"timeout": 3, "command_timeout": 3},
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
