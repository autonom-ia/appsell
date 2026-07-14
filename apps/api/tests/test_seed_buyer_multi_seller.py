from __future__ import annotations

import pytest

from appsell_api.seed_buyer_multi_seller import CONFIRMATION, require_environment


def configure_valid_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSELL_API_ENVIRONMENT", "local")
    monkeypatch.setenv("APPSELL_MULTI_SELLER_ENABLED", "true")
    monkeypatch.setenv(
        "APPSELL_API_IDENTITY_ISSUER", "https://identity.multi-seller.test.autonomia.site"
    )
    monkeypatch.setenv(
        "APPSELL_MULTI_SELLER_DATABASE_URL",
        "postgresql://postgres@localhost/appsell",
    )


@pytest.mark.parametrize("variable", ["PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"])
def test_multi_seller_seed_rejects_inherited_libpq_routing(
    monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    configure_valid_environment(monkeypatch)
    monkeypatch.setenv(variable, "203.0.113.10")

    with pytest.raises(RuntimeError, match="must not inherit libpq routing"):
        require_environment(CONFIRMATION)


def test_multi_seller_seed_accepts_explicit_disposable_local_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_valid_environment(monkeypatch)
    for variable in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
        monkeypatch.delenv(variable, raising=False)

    issuer, database_url = require_environment(CONFIRMATION)

    assert issuer == "https://identity.multi-seller.test.autonomia.site"
    assert database_url == "postgresql://postgres@localhost/appsell"
