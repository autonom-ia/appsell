import base64
import json
from typing import Any

import pytest
from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict

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

AWS_STORAGE: dict[str, Any] = {
    "storage_provider": "s3",
    "storage_quarantine_bucket": "appsell-quarantine-test",
    "storage_ready_bucket": "appsell-ready-test",
    "storage_prefix": "appsell/ci",
    "storage_region": "sa-east-1",
    "storage_allowed_origin": "http://localhost:3000",
    "malware_scanner_provider": "deterministic",
    "pdf_sandbox_socket": "/run/appsell-pdf/pdf.sock",
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
    assert Settings(environment="local").video_upload_enabled is False
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


def test_local_accepts_native_aws_s3_with_default_credential_chain() -> None:
    settings = Settings(environment="local", **AWS_STORAGE)

    assert settings.storage_configured
    assert settings.storage_quarantine_bucket == "appsell-quarantine-test"
    assert settings.storage_ready_bucket == "appsell-ready-test"
    assert settings.storage_prefix == "appsell/ci"
    assert settings.storage_uses_custom_endpoint is False
    assert settings.storage_control_access_key == ""
    assert settings.storage_control_secret_key == ""


@pytest.mark.parametrize(
    "overrides",
    [
        {"storage_prefix": "/appsell/ci"},
        {"storage_prefix": "appsell/../ci"},
        {"storage_prefix": "appsell/ci\ninvalid"},
        {"storage_quarantine_bucket": "Invalid_Bucket"},
        {"storage_quarantine_bucket": "192.168.0.1"},
        {"storage_ready_bucket": "appsell-quarantine-test"},
        {"storage_control_access_key": "access-without-secret"},
        {"storage_control_session_token": "token-without-access-keys"},
        {"storage_internal_endpoint": "https://s3.internal.example.test"},
    ],
)
def test_native_aws_s3_rejects_unsafe_or_ambiguous_configuration(
    overrides: dict[str, str],
) -> None:
    with pytest.raises(ValidationError):
        Settings(environment="local", **{**AWS_STORAGE, **overrides})


def test_native_aws_s3_rejects_single_bucket_before_startup() -> None:
    with pytest.raises(ValidationError, match="single-bucket S3 storage is unsupported"):
        Settings(
            environment="local",
            **{
                **AWS_STORAGE,
                "storage_bucket": "appsell-private-test",
                "storage_quarantine_bucket": "",
                "storage_ready_bucket": "",
            },
        )


def test_native_aws_s3_rejects_equal_physical_buckets_before_startup() -> None:
    with pytest.raises(ValidationError, match="distinct physical buckets"):
        Settings(
            environment="local",
            **{**AWS_STORAGE, "storage_ready_bucket": "appsell-quarantine-test"},
        )


def test_prerastered_legacy_viewer_does_not_require_pdf_parser() -> None:
    settings = Settings(
        environment="ci",
        private_learning_asset_root="/workspace/sell/output/pdf/codex",
    )
    assert settings.pdf_sandbox_required is False


@pytest.mark.parametrize(
    "root",
    ["relative/output/pdf", "/workspace/sell/../other", "/workspace/sell/output\0/pdf"],
)
def test_protected_pdf_viewer_rejects_unsafe_private_root(root: str) -> None:
    with pytest.raises(ValidationError, match="absolute safe path"):
        Settings(
            environment="ci",
            private_learning_asset_root=root,
        )


def test_video_upload_requires_s3_and_a_local_unix_socket_sandbox() -> None:
    with pytest.raises(ValidationError, match="video upload requires"):
        Settings(environment="local", video_upload_enabled=True)
    with pytest.raises(ValidationError, match="video upload requires"):
        Settings(
            environment="production",
            video_upload_enabled=True,
            database_url="postgresql+asyncpg://runtime@db.example.test:5432/appsell?ssl=require",
            **INTERNAL_AUTH,
            **REMOTE_STORAGE,
        )
    storage = {
        **REMOTE_STORAGE,
        "malware_scanner_provider": "deterministic",
        "malware_scanner_socket": "",
        "pdf_sandbox_url": "",
        "pdf_sandbox_socket": "/run/appsell-pdf/pdf.sock",
    }
    assert Settings(environment="local", video_upload_enabled=True, **storage).video_upload_enabled


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


def test_capacity_product_create_telemetry_is_default_off_and_local_ci_only() -> None:
    assert Settings(environment="local").capacity_product_create_telemetry is False
    assert (
        Settings(
            environment="ci", capacity_product_create_telemetry=True
        ).capacity_product_create_telemetry
        is True
    )
    with pytest.raises(ValidationError, match="telemetry is forbidden"):
        Settings(
            environment="staging",
            capacity_product_create_telemetry=True,
            **INTERNAL_AUTH,
            **REMOTE_STORAGE,
        )
    with pytest.raises(ValidationError, match="telemetry is forbidden"):
        Settings(
            environment="production",
            database_url=("postgresql+asyncpg://runtime@db.example.test:5432/appsell?ssl=require"),
            capacity_product_create_telemetry=True,
            **INTERNAL_AUTH,
            **REMOTE_STORAGE,
        )


def _partial_auth_error(settings_cls: type[Settings], secret: str) -> str:
    with pytest.raises(ValidationError) as exc_info:
        settings_cls(environment="local", service_credential_b64=secret)
    rendered = str(exc_info.value)
    # The partial-internal-auth branch must be the one that fired.
    assert "internal request authentication must be configured completely" in rendered
    return rendered


def test_config_hides_secret_input_values_in_validation_errors() -> None:
    # A model-level (mode="after") validation error records the whole input,
    # which includes secret env values. hide_input_in_errors must redact it so
    # startup ValidationErrors never echo credentials into logs/tracebacks.
    # This test is load-bearing: a flag-off variant of the SAME model must leak
    # the sentinel, and the shipped model must not — so removing the flag fails.
    # The sentinel is kept short and is the last kwarg so it survives pydantic's
    # input-repr truncation; otherwise `not in` would pass trivially (false green).
    secret_sentinel = "LEAKCANARY42"  # noqa: S105

    class LeakySettings(Settings):
        model_config = SettingsConfigDict(
            env_prefix="APPSELL_API_",
            env_file=".env",
            extra="ignore",
            hide_input_in_errors=False,
        )

    leaked = _partial_auth_error(LeakySettings, secret_sentinel)
    assert secret_sentinel in leaked  # guard proves the sentinel is observable when unredacted

    shipped = _partial_auth_error(Settings, secret_sentinel)
    assert secret_sentinel not in shipped  # shipped config must redact it
