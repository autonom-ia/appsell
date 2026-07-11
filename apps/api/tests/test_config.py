import base64
import json

import pytest
from pydantic import ValidationError

from appsell_api.config import Settings

INTERNAL_AUTH = {
    "internal_issuer": "appsell-bff",
    "internal_audience": "appsell-api",
    "identity_issuer": "https://identity.autonomia.site",
    "internal_hmac_keyring_b64_json": json.dumps(
        [
            {
                "id": "test-v1",
                "key": base64.b64encode(bytes(range(32))).decode(),
                "active": True,
            }
        ]
    ),
    "context_hmac_key_b64": base64.b64encode(bytes(range(32, 64))).decode(),
    "service_credential_b64": base64.b64encode(bytes(range(64, 96))).decode(),
}


def test_production_rejects_local_database() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production", **INTERNAL_AUTH)


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
        Settings(environment="production", database_url=database_url, **INTERNAL_AUTH)


def test_production_accepts_remote_postgres_with_tls() -> None:
    settings = Settings(
        environment="production",
        database_url="postgresql+asyncpg://runtime@db.example.test:5432/appsell?ssl=require",
        **INTERNAL_AUTH,
    )
    assert settings.is_production
