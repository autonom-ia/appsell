import base64
import json
from typing import Any

import pytest
from pydantic import ValidationError

from appsell_api.config import Settings

INTERNAL_AUTH: dict[str, Any] = {
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

REMOTE_STORAGE: dict[str, Any] = {
    "storage_provider": "s3",
    "storage_internal_endpoint": "https://s3.internal.example.test",
    "storage_public_endpoint": "https://assets.example.test",
    "storage_region": "eu-west-1",
    "storage_quarantine_bucket": "appsell-quarantine",
    "storage_ready_bucket": "appsell-ready",
    "storage_control_access_key": "test-control",
    "storage_control_secret_key": "test-secret",
    "storage_allowed_origin": "https://sell.autonomia.site",
    "malware_scanner_provider": "clamav",
    "malware_scanner_socket": "/run/appsell-clamav/clamd.sock",
    "pdf_sandbox_url": "https://pdf-sandbox.internal/normalize",
}


def test_production_rejects_local_database() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production", **INTERNAL_AUTH, **REMOTE_STORAGE)


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
        Settings(
            environment="production",
            database_url=database_url,
            **INTERNAL_AUTH,
            **REMOTE_STORAGE,
        )


def test_production_accepts_remote_postgres_with_tls() -> None:
    settings = Settings(
        environment="production",
        database_url="postgresql+asyncpg://runtime@db.example.test:5432/appsell?ssl=require",
        **INTERNAL_AUTH,
        **REMOTE_STORAGE,
    )
    assert settings.is_production
    assert settings.storage_configured


def test_local_allows_storage_disabled_but_rejects_partial_s3() -> None:
    assert Settings(environment="local").storage_configured is False
    assert (
        Settings(
            environment="local", pdf_sandbox_socket="/run/appsell-pdf/pdf.sock"
        ).storage_configured
        is False
    )
    with pytest.raises(ValidationError):
        Settings(environment="local", storage_provider="s3")


def test_local_accepts_pdf_sandbox_unix_socket() -> None:
    storage = {
        **REMOTE_STORAGE,
        "malware_scanner_provider": "deterministic",
        "malware_scanner_socket": "",
        "pdf_sandbox_url": "",
        "pdf_sandbox_socket": "/run/appsell-pdf/pdf.sock",
    }
    settings = Settings(environment="local", **storage)
    assert settings.pdf_sandbox_socket == "/run/appsell-pdf/pdf.sock"


@pytest.mark.parametrize(
    "socket_path",
    [
        "relative/pdf.sock",
        "/run/appsell-pdf/not-pdf.socket",
        "/run/appsell-pdf/../pdf.sock",
    ],
)
def test_local_rejects_unsafe_pdf_sandbox_socket(socket_path: str) -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="local",
            storage_provider="s3",
            pdf_sandbox_socket=socket_path,
            **{key: value for key, value in REMOTE_STORAGE.items() if key != "storage_provider"},
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"storage_ready_bucket": "appsell-quarantine"},
        {"storage_public_endpoint": "http://127.0.0.1:8333"},
        {"storage_public_endpoint": "https://user:secret@assets.example.test"},
        {"malware_scanner_provider": "deterministic"},
        {"pdf_sandbox_url": "http://127.0.0.1:8081/normalize"},
        {"pdf_sandbox_socket": "/run/appsell-pdf/pdf.sock"},
    ],
)
def test_production_rejects_unsafe_storage(overrides: dict[str, str]) -> None:
    storage = {**REMOTE_STORAGE, **overrides}
    with pytest.raises(ValidationError):
        Settings(
            environment="production",
            database_url="postgresql+asyncpg://runtime@db.example.test:5432/appsell?ssl=require",
            **INTERNAL_AUTH,
            **storage,
        )


def test_staging_requires_storage_and_real_scanner() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="staging", **INTERNAL_AUTH)


def test_storage_ttls_cannot_be_weakened() -> None:
    with pytest.raises(ValidationError):
        Settings(storage_presign_put_ttl_seconds=301)
    with pytest.raises(ValidationError):
        Settings(storage_presign_get_ttl_seconds=61)


def test_storage_readiness_cache_must_outlive_health_probe_interval() -> None:
    with pytest.raises(ValidationError, match="staleness"):
        Settings(
            storage_health_probe_interval_seconds=30,
            storage_readiness_stale_after_seconds=30,
        )


def test_database_application_name_is_bounded_and_machine_readable() -> None:
    assert Settings(database_application_name="appsell_capacity_api").database_application_name == (
        "appsell_capacity_api"
    )
    with pytest.raises(ValidationError):
        Settings(database_application_name="unsafe name; select 1")
