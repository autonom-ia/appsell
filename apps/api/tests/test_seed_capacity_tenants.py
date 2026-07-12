from __future__ import annotations

from uuid import UUID

import pytest

from appsell_api.seed_capacity_tenants import (
    CAPACITY_CONFIRMATION,
    CAPACITY_TENANTS,
    identity_id,
    require_environment,
    snapshot_for,
    tenant_manifest,
)


def test_capacity_manifest_is_deterministic_and_tenant_scoped() -> None:
    issuer = "https://identity.capacity.test.autonomia.site"
    first = tenant_manifest(issuer, 0)
    repeated = tenant_manifest(issuer, 0)
    last = tenant_manifest(issuer, CAPACITY_TENANTS - 1)

    assert first == repeated
    assert first["seller_id"] != last["seller_id"]
    assert first["owner_identity_id"] == str(identity_id(issuer, "capacity-owner-000"))
    assert UUID(str(first["buyer_identity_id"]))
    assert first["seller_slug"] == "capacity-seller-000"
    assert last["seller_slug"] == "capacity-seller-099"


def test_capacity_snapshot_is_schema_three_with_verified_legacy_asset() -> None:
    tenant = tenant_manifest("https://identity.capacity.test.autonomia.site", 7)
    snapshot = snapshot_for({key: str(value) for key, value in tenant.items()})

    assert snapshot["schema_version"] == 3
    assert snapshot["seller_organization_id"] == tenant["seller_id"]
    course = snapshot["courses"][0]
    assert course["id"] == tenant["course_id"]
    assets = course["modules"][0]["lessons"][0]["assets"]
    assert assets == [
        {
            "id": tenant["asset_id"],
            "version": 1,
            "title": tenant["asset_title"],
            "file_name": tenant["asset_file_name"],
            "media_type": "application/pdf",
            "byte_size": tenant["asset_bytes"],
            "sha256": tenant["asset_sha256"],
            "position": 0,
        }
    ]


def test_capacity_assets_rotate_five_distinct_payloads_without_adjacent_match() -> None:
    issuer = "https://identity.capacity.test.autonomia.site"
    tenants = [tenant_manifest(issuer, ordinal) for ordinal in range(CAPACITY_TENANTS)]
    hashes = [str(tenant["asset_sha256"]) for tenant in tenants]

    assert len(set(hashes)) == 5
    assert all(
        hashes[index] != hashes[(index + 1) % CAPACITY_TENANTS] for index in range(CAPACITY_TENANTS)
    )


@pytest.mark.parametrize(
    ("environment", "database_url", "message"),
    [
        ("production", "postgresql://postgres@127.0.0.1/appsell", "local or ci"),
        ("local", "postgresql://postgres@10.0.0.4/appsell", "loopback-only"),
        (
            "local",
            "postgresql://postgres@localhost/appsell?hostaddr=10.0.0.4",
            "hostaddr must be loopback-only",
        ),
        (
            "local",
            "postgresql://postgres@localhost/appsell?service=remote",
            "service indirection",
        ),
        ("local", "sqlite:///tmp/appsell.db", "PostgreSQL DSN"),
    ],
)
def test_capacity_seed_fails_closed_outside_disposable_local_database(
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
    database_url: str,
    message: str,
) -> None:
    monkeypatch.setenv("APPSELL_API_ENVIRONMENT", environment)
    monkeypatch.setenv(
        "APPSELL_API_IDENTITY_ISSUER", "https://identity.capacity.test.autonomia.site"
    )
    monkeypatch.setenv("APPSELL_CAPACITY_DATABASE_URL", database_url)

    with pytest.raises(RuntimeError, match=message):
        require_environment(CAPACITY_CONFIRMATION)


def test_capacity_seed_rejects_wrong_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSELL_API_ENVIRONMENT", "local")
    monkeypatch.setenv(
        "APPSELL_API_IDENTITY_ISSUER", "https://identity.capacity.test.autonomia.site"
    )
    monkeypatch.setenv("APPSELL_CAPACITY_DATABASE_URL", "postgresql://postgres@127.0.0.1/appsell")

    with pytest.raises(RuntimeError, match="confirmation mismatch"):
        require_environment("wrong")


@pytest.mark.parametrize("variable", ["PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"])
def test_capacity_seed_rejects_inherited_libpq_routing(
    monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    monkeypatch.setenv("APPSELL_API_ENVIRONMENT", "local")
    monkeypatch.setenv(
        "APPSELL_API_IDENTITY_ISSUER", "https://identity.capacity.test.autonomia.site"
    )
    monkeypatch.setenv("APPSELL_CAPACITY_DATABASE_URL", "postgresql://postgres@localhost/appsell")
    monkeypatch.setenv(variable, "203.0.113.10")

    with pytest.raises(RuntimeError, match="must not inherit libpq routing"):
        require_environment(CAPACITY_CONFIRMATION)
