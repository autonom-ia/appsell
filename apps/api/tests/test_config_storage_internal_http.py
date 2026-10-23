"""O endpoint interno do storage em http, só numa rede isolada (#461).

Fora de local/CI os dois endpoints do storage exigem https. Na produção isso
obrigava o controle a sair pelo host público e voltar pelo Traefik, e a borda do
#477 não podia fechá-lo. A flag `storage_internal_http_allowed` aceita http só
no endpoint interno e só para um nome de serviço da rede do Swarm.
"""

from typing import Any

import pytest
from pydantic import ValidationError
from test_config import INTERNAL_AUTH, REMOTE_STORAGE

from appsell_api.config import Settings

PRODUCTION: dict[str, Any] = {
    "environment": "production",
    "database_url": "postgresql+asyncpg://runtime@db.example.test:5432/appsell?ssl=require",
    **INTERNAL_AUTH,
}


def settings(**overrides: Any) -> Settings:
    return Settings(**{**PRODUCTION, **REMOTE_STORAGE, **overrides})


def test_flag_is_off_by_default() -> None:
    assert settings().storage_internal_http_allowed is False


def test_flag_off_rejects_http_internal_endpoint() -> None:
    with pytest.raises(ValidationError, match="remote HTTPS S3 endpoints are required"):
        settings(storage_internal_endpoint="http://appsell-storage:9000")


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://appsell.storage:9000",
        "http://minio.autonomia.site",
        "http://appsell-storage.:9000",
    ],
)
def test_flag_on_rejects_http_host_with_a_dot(endpoint: str) -> None:
    with pytest.raises(ValidationError, match="internal HTTP storage endpoint"):
        settings(storage_internal_http_allowed=True, storage_internal_endpoint=endpoint)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://10.11.0.7:9000",
        "http://[fd00::7]:9000",
        "http://2130706433:9000",
        "http://0x7f000001:9000",
    ],
)
def test_flag_on_rejects_http_ip_address(endpoint: str) -> None:
    with pytest.raises(ValidationError, match="internal HTTP storage endpoint"):
        settings(storage_internal_http_allowed=True, storage_internal_endpoint=endpoint)


@pytest.mark.parametrize("endpoint", ["http://localhost:9000", "http://LOCALHOST:9000"])
def test_flag_on_rejects_http_localhost(endpoint: str) -> None:
    with pytest.raises(ValidationError, match="internal HTTP storage endpoint"):
        settings(storage_internal_http_allowed=True, storage_internal_endpoint=endpoint)


def test_flag_on_rejects_http_host_with_underscore() -> None:
    # O MinIO recusa Host com "_" (400 InvalidRequest, lido na produção em
    # 23/10/2026): o nome sell-autonomia_storage não serve de endpoint.
    with pytest.raises(ValidationError, match="internal HTTP storage endpoint"):
        settings(
            storage_internal_http_allowed=True,
            storage_internal_endpoint="http://sell-autonomia_storage:9000",
        )


def test_flag_on_accepts_http_service_name() -> None:
    resolved = settings(
        storage_internal_http_allowed=True,
        storage_internal_endpoint="http://appsell-storage:9000",
    )
    assert resolved.storage_internal_endpoint == "http://appsell-storage:9000"


def test_flag_on_still_rejects_http_public_endpoint() -> None:
    with pytest.raises(ValidationError, match="remote HTTPS S3 endpoints are required"):
        settings(
            storage_internal_http_allowed=True,
            storage_internal_endpoint="http://appsell-storage:9000",
            storage_public_endpoint="http://appsell-storage:9000",
        )


def test_flag_on_still_rejects_http_allowed_origin() -> None:
    with pytest.raises(ValidationError, match="remote HTTPS storage origin is required"):
        settings(
            storage_internal_http_allowed=True,
            storage_internal_endpoint="http://appsell-storage:9000",
            storage_allowed_origin="http://sell.autonomia.site",
        )


def test_flag_on_accepts_https_internal_endpoint() -> None:
    resolved = settings(storage_internal_http_allowed=True)
    assert resolved.storage_internal_endpoint == REMOTE_STORAGE["storage_internal_endpoint"]


def test_flag_on_still_rejects_local_https_internal_endpoint() -> None:
    with pytest.raises(ValidationError, match="remote HTTPS S3 endpoints are required"):
        settings(
            storage_internal_http_allowed=True,
            storage_internal_endpoint="https://localhost:9000",
        )
