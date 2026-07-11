import ipaddress
from functools import lru_cache
from typing import Literal, Self

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
    log_level: str = "INFO"
    internal_issuer: str = ""
    internal_audience: str = ""
    identity_issuer: str = ""
    internal_hmac_keyring_b64_json: str = ""
    context_hmac_key_b64: str = ""
    service_credential_b64: str = ""

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @model_validator(mode="after")
    def reject_local_database_in_production(self) -> Self:
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
        if not self.is_production:
            return self
        url = make_url(self.database_url)
        if url.drivername != "postgresql+asyncpg":
            raise ValueError("production database URL must use postgresql+asyncpg")
        host = (url.host or "").lower().rstrip(".")
        if not host or host == "localhost" or host.endswith(".localhost") or host == "postgres":
            raise ValueError("production database URL must not target a local host")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and address.is_loopback:
            raise ValueError("production database URL must not target loopback")
        if url.query.get("ssl") not in {"require", "verify-ca", "verify-full"}:
            raise ValueError("production database URL must require TLS")
        return self

    @property
    def internal_auth_configured(self) -> bool:
        return bool(self.internal_issuer)


@lru_cache
def get_settings() -> Settings:
    return Settings()
