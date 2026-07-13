from unittest.mock import Mock

import appsell_api.db as db_module
from appsell_api.config import Settings


def test_api_pool_keeps_backpressure_inside_the_postgres_budget(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    engine = Mock()
    create_async_engine = Mock(return_value=engine)
    monkeypatch.setattr(db_module, "create_async_engine", create_async_engine)

    settings = Settings(
        database_url="postgresql+asyncpg://runtime@db.example/appsell",
        database_application_name="appsell_api_test",
    )

    assert db_module.create_engine(settings) is engine
    create_async_engine.assert_called_once_with(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=0,
        pool_timeout=3,
        connect_args={
            "timeout": 3,
            "command_timeout": 3,
            "server_settings": {"application_name": settings.database_application_name},
        },
    )
