import pytest
from pydantic import ValidationError

from appsell_api.config import Settings


def test_production_rejects_local_database() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production")


def test_unknown_environment_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="prod")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+asyncpg://runtime@[::1]:5432/appsell?ssl=require",
        "sqlite+aiosqlite:///tmp/appsell.db",
        "postgresql+asyncpg://runtime@db.example.test:5432/appsell",
    ],
)
def test_production_rejects_wrong_driver_loopback_or_missing_tls(database_url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production", database_url=database_url)


def test_production_accepts_remote_postgres_with_tls() -> None:
    settings = Settings(
        environment="production",
        database_url="postgresql+asyncpg://runtime@db.example.test:5432/appsell?ssl=require",
    )
    assert settings.is_production
