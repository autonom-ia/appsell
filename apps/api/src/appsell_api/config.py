import ipaddress
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APPSELL_API_",
        env_file=".env",
        extra="ignore",
    )

    environment: Literal["local", "ci", "staging", "production"] = "local"
    database_url: str = Field(
        default="postgresql+asyncpg://appsell_runtime@127.0.0.1:55432/appsell"
    )
    database_application_name: str = Field(default="appsell_api", pattern=r"^[a-z][a-z0-9_]{2,31}$")
    log_level: str = "INFO"
    internal_issuer: str = ""
    internal_audience: str = ""
    identity_issuer: str = ""
    internal_hmac_keyring_b64_json: str = ""
    context_hmac_key_b64: str = ""
    service_credential_b64: str = ""
    storage_provider: Literal["disabled", "s3"] = "disabled"
    storage_internal_endpoint: str = ""
    storage_public_endpoint: str = ""
    storage_region: str = "us-east-1"
    storage_path_style: bool = True
    storage_quarantine_bucket: str = ""
    storage_ready_bucket: str = ""
    storage_control_access_key: str = ""
    storage_control_secret_key: str = ""
    storage_allowed_origin: str = ""
    storage_presign_put_ttl_seconds: int = Field(default=300, ge=30, le=300)
    storage_presign_get_ttl_seconds: int = Field(default=60, ge=15, le=60)
    storage_connect_timeout_seconds: int = Field(default=2, ge=1, le=5)
    storage_read_timeout_seconds: int = Field(default=15, ge=2, le=30)
    storage_health_probe_interval_seconds: int = Field(default=10, ge=2, le=60)
    storage_control_audit_interval_seconds: int = Field(default=60, ge=10, le=3600)
    storage_readiness_stale_after_seconds: int = Field(default=30, ge=5, le=120)
    malware_scanner_provider: Literal["disabled", "deterministic", "clamav"] = "disabled"
    malware_scanner_socket: str = ""
    pdf_sandbox_url: str = ""
    pdf_sandbox_socket: str = ""
    private_learning_asset_root: str = ""
    capacity_product_create_telemetry: bool = False
    video_upload_enabled: bool = False
    webhook_local_keyring_b64_json: str = ""
    # Opt-in explícito para keyring de webhook por env fora de local/ci
    # (provisionamento prod do checkout, Refs #96). Mantém todos os checks de
    # independência do WebhookSecretResolver; sem esta flag o comportamento
    # fail-closed original permanece.
    webhook_env_keyring_allowed: bool = False
    webhook_forbidden_key_sha256_json: str = "[]"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def pdf_sandbox_required(self) -> bool:
        return self.storage_configured

    @model_validator(mode="after")
    def validate_fail_closed_environment(self) -> Self:
        if self.private_learning_asset_root:
            asset_root = Path(self.private_learning_asset_root)
            if (
                "\0" in self.private_learning_asset_root
                or not asset_root.is_absolute()
                or ".." in asset_root.parts
            ):
                raise ValueError("private learning asset root must be an absolute safe path")
        if (
            self.webhook_local_keyring_b64_json
            and self.environment not in {"local", "ci"}
            and not self.webhook_env_keyring_allowed
        ):
            raise ValueError("local webhook secrets are forbidden outside local/CI")
        if self.capacity_product_create_telemetry and self.environment not in {"local", "ci"}:
            raise ValueError("capacity product-create telemetry is forbidden outside local/CI")
        if self.storage_readiness_stale_after_seconds <= self.storage_health_probe_interval_seconds:
            raise ValueError("storage readiness staleness must exceed the health probe interval")
        internal_values = (
            self.internal_issuer,
            self.internal_audience,
            self.identity_issuer,
            self.internal_hmac_keyring_b64_json,
            self.context_hmac_key_b64,
            self.service_credential_b64,
        )
        if any(internal_values) and not all(internal_values):
            raise ValueError("internal request authentication must be configured completely")
        if self.environment in {"staging", "production"} and not all(internal_values):
            raise ValueError("internal request authentication is required outside local/CI")
        if self.storage_provider == "s3":
            storage_values = (
                self.storage_internal_endpoint,
                self.storage_public_endpoint,
                self.storage_region,
                self.storage_quarantine_bucket,
                self.storage_ready_bucket,
                self.storage_control_access_key,
                self.storage_control_secret_key,
                self.storage_allowed_origin,
            )
            if not all(storage_values):
                raise ValueError("S3 storage must be configured completely")
            if self.storage_quarantine_bucket == self.storage_ready_bucket:
                raise ValueError("S3 quarantine and ready buckets must be distinct")
            for endpoint in (self.storage_internal_endpoint, self.storage_public_endpoint):
                parsed = urlparse(endpoint)
                if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                    raise ValueError("S3 endpoints must be absolute HTTP URLs")
                if parsed.username or parsed.password or parsed.query or parsed.fragment:
                    raise ValueError("S3 endpoints must not contain credentials or query data")
            allowed_origin = urlparse(self.storage_allowed_origin)
            if (
                allowed_origin.scheme not in {"http", "https"}
                or not allowed_origin.hostname
                or allowed_origin.username
                or allowed_origin.password
                or allowed_origin.path not in {"", "/"}
                or allowed_origin.query
                or allowed_origin.fragment
            ):
                raise ValueError("storage allowed origin must be an exact HTTP origin")
            if bool(self.pdf_sandbox_url) == bool(self.pdf_sandbox_socket):
                raise ValueError("configure exactly one isolated PDF parser transport")
            if self.pdf_sandbox_url:
                sandbox = urlparse(self.pdf_sandbox_url)
                if (
                    sandbox.scheme not in {"http", "https"}
                    or not sandbox.hostname
                    or sandbox.username
                    or sandbox.password
                    or sandbox.path != "/normalize"
                    or sandbox.query
                    or sandbox.fragment
                    or _is_local_host(sandbox.hostname)
                ):
                    raise ValueError("an isolated PDF parser service is required with S3 storage")
            else:
                socket_path = Path(self.pdf_sandbox_socket)
                socket_root = Path("/run/appsell-pdf")
                if (
                    "\0" in self.pdf_sandbox_socket
                    or not socket_path.is_absolute()
                    or ".." in socket_path.parts
                    or socket_path.name != "pdf.sock"
                    or socket_path.parent.resolve(strict=False) != socket_root.resolve(strict=False)
                ):
                    raise ValueError("PDF parser socket path is invalid")
        elif any(
            (
                self.storage_internal_endpoint,
                self.storage_public_endpoint,
                self.storage_quarantine_bucket,
                self.storage_ready_bucket,
                self.storage_control_access_key,
                self.storage_control_secret_key,
                self.storage_allowed_origin,
            )
        ):
            raise ValueError("storage values require storage_provider=s3")

        if self.video_upload_enabled and (
            self.storage_provider != "s3" or not self.pdf_sandbox_socket
        ):
            raise ValueError("video upload requires S3 and an isolated Unix socket media sandbox")

        if self.malware_scanner_provider == "clamav":
            scanner_socket = Path(self.malware_scanner_socket)
            scanner_root = Path("/run/appsell-clamav")
            if (
                "\0" in self.malware_scanner_socket
                or not scanner_socket.is_absolute()
                or ".." in scanner_socket.parts
                or scanner_socket.name != "clamd.sock"
                or scanner_socket.parent.resolve(strict=False) != scanner_root.resolve(strict=False)
            ):
                raise ValueError("real malware scanner socket path is invalid")
        elif self.malware_scanner_socket:
            raise ValueError("malware scanner socket requires clamav mode")

        if self.environment in {"staging", "production"}:
            if self.storage_provider != "s3":
                raise ValueError("S3 storage is required outside local/CI")
            if self.malware_scanner_provider != "clamav":
                raise ValueError("a real malware scanner is required outside local/CI")
            sandbox = urlparse(self.pdf_sandbox_url)
            if self.pdf_sandbox_url and sandbox.scheme != "https":
                raise ValueError("HTTPS PDF parser transport is required outside local/CI")
            for endpoint in (self.storage_internal_endpoint, self.storage_public_endpoint):
                parsed = urlparse(endpoint)
                if parsed.scheme != "https" or _is_local_host(parsed.hostname or ""):
                    raise ValueError("remote HTTPS S3 endpoints are required outside local/CI")
            origin = urlparse(self.storage_allowed_origin)
            if origin.scheme != "https" or _is_local_host(origin.hostname or ""):
                raise ValueError("remote HTTPS storage origin is required outside local/CI")
        elif self.malware_scanner_provider == "clamav":
            # Local/CI may exercise a real adapter, but it must still opt into S3 explicitly.
            if self.storage_provider != "s3":
                raise ValueError("real scanner mode requires configured S3 storage")

        if self.is_production:
            url = make_url(self.database_url)
            if url.drivername != "postgresql+asyncpg":
                raise ValueError("production database URL must use postgresql+asyncpg")
            host = (url.host or "").lower().rstrip(".")
            if _is_local_host(host):
                raise ValueError("production database URL must not target a local host")
            if url.query.get("ssl") not in {"require", "verify-ca", "verify-full"}:
                raise ValueError("production database URL must require TLS")
        return self

    @property
    def internal_auth_configured(self) -> bool:
        return bool(self.internal_issuer)

    @property
    def storage_configured(self) -> bool:
        return self.storage_provider == "s3"


def _is_local_host(host: str) -> bool:
    normalized = host.lower().rstrip(".")
    if not normalized or normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    if normalized in {"postgres", "seaweedfs", "seaweed"}:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


@lru_cache
def get_settings() -> Settings:
    return Settings()
