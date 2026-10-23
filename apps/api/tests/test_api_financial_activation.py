"""Route contract of the financial activation endpoints (#382).

Unit tests over the scripted session of test_api_payout.py: they prove the
ROUTE's own gates — the per-store flag, the shared pair coming only from the
API env, membership before any activation SQL, and the outcome mapping. The
database guarantees are in test_postgres_financial_activation.py.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from test_api_payout import (
    OTHER_SELLER_ID,
    SELLER_ID,
    FakeAuth,
    FakeResult,
    client_for,
    db_error,
)

from appsell_api.payout_routes import (
    FinancialActivationConfigError,
    financial_activation_config,
)

OPERATOR = UUID("63000000-0000-4000-8000-000000000001")
CATALOG_ITEM = UUID("63000000-0000-4000-8000-000000000002")
PATH = f"/v1/me/sellers/{SELLER_ID}/financial-activation"


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv("APPSELL_FINANCIAL_SELF_SERVICE_SELLER_IDS", f" {SELLER_ID} ")
    monkeypatch.setenv("APPSELL_FINANCIAL_SHARED_OPERATOR_ID", str(OPERATOR))
    monkeypatch.setenv("APPSELL_FINANCIAL_SHARED_CATALOG_ITEM_ID", str(CATALOG_ITEM))
    monkeypatch.delenv("APPSELL_FINANCIAL_ACTIVATION_REQUIRES_PAYOUT_ACCOUNT", raising=False)
    return monkeypatch


def situation(*, bound: bool, payout_ready: bool, channel_ready: bool) -> FakeResult:
    return FakeResult(
        one_or_none={
            "bound": bound,
            "payout_ready": payout_ready,
            "channel_ready": channel_ready,
        }
    )


async def post(auth: FakeAuth, content: bytes = b"{}") -> Any:
    async with client_for(auth) as client:
        return await client.post(PATH, content=content)


async def get(auth: FakeAuth) -> Any:
    async with client_for(auth) as client:
        return await client.get(PATH)


@pytest.mark.asyncio
async def test_activate_sends_the_env_pair_never_the_body(env: pytest.MonkeyPatch) -> None:
    auth = FakeAuth(seller_id=SELLER_ID, script=["seller_owner", "activated"])
    response = await post(auth)

    assert response.status_code == 200
    assert response.json() == {"status": "active", "changed": True}
    assert auth.capabilities == ["commerce.payout.manage"]
    sql, params = auth.session.calls[1]
    assert "activate_seller_financial" in sql
    assert params == {
        "seller_id": SELLER_ID,
        "operator_id": OPERATOR,
        "catalog_item_id": CATALOG_ITEM,
        "require_payout_ready": False,
        "request_id": auth.request_id,
    }


@pytest.mark.asyncio
async def test_a_body_with_any_field_is_refused(env: pytest.MonkeyPatch) -> None:
    # The pair must never come from the browser: a body that tries is a 422.
    auth = FakeAuth(seller_id=SELLER_ID, script=[])
    response = await post(auth, f'{{"operator_id":"{uuid4()}"}}'.encode())

    assert response.status_code == 422
    assert auth.session.calls == []
    assert auth.rejected_requests == 1


@pytest.mark.asyncio
async def test_an_existing_binding_is_reported_unchanged(env: pytest.MonkeyPatch) -> None:
    auth = FakeAuth(seller_id=SELLER_ID, script=["seller_owner", "already_active"])
    response = await post(auth)

    assert response.status_code == 200
    assert response.json() == {"status": "active", "changed": False}


@pytest.mark.asyncio
async def test_a_store_outside_the_flag_cannot_activate(env: pytest.MonkeyPatch) -> None:
    # The flag is per store and empty by default: Hub2You stays out.
    env.setenv("APPSELL_FINANCIAL_SELF_SERVICE_SELLER_IDS", str(OTHER_SELLER_ID))
    auth = FakeAuth(seller_id=SELLER_ID, script=["seller_owner"])
    response = await post(auth)

    assert response.status_code == 403
    assert response.json()["detail"] == "financial_self_service_disabled"
    assert len(auth.session.calls) == 1


@pytest.mark.asyncio
async def test_without_the_flag_env_nobody_activates(env: pytest.MonkeyPatch) -> None:
    env.delenv("APPSELL_FINANCIAL_SELF_SERVICE_SELLER_IDS")
    auth = FakeAuth(seller_id=SELLER_ID, script=["seller_owner"])
    response = await post(auth)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_only_the_owner_activates(env: pytest.MonkeyPatch) -> None:
    auth = FakeAuth(seller_id=SELLER_ID, script=["seller_admin"])
    response = await post(auth)

    assert response.status_code == 403
    assert len(auth.session.calls) == 1


@pytest.mark.asyncio
async def test_an_envelope_for_another_seller_is_refused(env: pytest.MonkeyPatch) -> None:
    auth = FakeAuth(seller_id=OTHER_SELLER_ID, script=[])
    response = await post(auth)

    assert response.status_code == 401
    assert auth.session.calls == []


@pytest.mark.asyncio
async def test_missing_pair_is_unconfigured(env: pytest.MonkeyPatch) -> None:
    env.delenv("APPSELL_FINANCIAL_SHARED_OPERATOR_ID")
    env.delenv("APPSELL_FINANCIAL_SHARED_CATALOG_ITEM_ID")
    auth = FakeAuth(seller_id=SELLER_ID, script=["seller_owner"])
    response = await post(auth)

    assert response.status_code == 503
    assert response.json()["detail"] == "financial_activation_unconfigured"
    assert len(auth.session.calls) == 1


@pytest.mark.asyncio
async def test_the_439_flag_reaches_the_database(env: pytest.MonkeyPatch) -> None:
    env.setenv("APPSELL_FINANCIAL_ACTIVATION_REQUIRES_PAYOUT_ACCOUNT", "true")
    auth = FakeAuth(seller_id=SELLER_ID, script=["seller_owner", "payout_account_not_ready"])
    response = await post(auth)

    assert response.status_code == 409
    assert response.json()["detail"] == "payout_account_not_ready"
    assert auth.session.calls[1][1]["require_payout_ready"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "code", "detail"),
    [
        ("channel_unavailable", 503, "financial_channel_unavailable"),
        ("seller_inactive", 403, "forbidden"),
        ("unexpected", 503, "payout_unavailable"),
    ],
)
async def test_refusals_map_to_http(
    env: pytest.MonkeyPatch, outcome: str, code: int, detail: str
) -> None:
    auth = FakeAuth(seller_id=SELLER_ID, script=["seller_owner", outcome])
    response = await post(auth)

    assert response.status_code == code
    assert response.json()["detail"] == detail


@pytest.mark.asyncio
async def test_database_denial_is_forbidden(env: pytest.MonkeyPatch) -> None:
    auth = FakeAuth(seller_id=SELLER_ID, script=["seller_owner", db_error("42501")])
    response = await post(auth)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_read_outside_the_flag_says_only_that(env: pytest.MonkeyPatch) -> None:
    # Nothing new reaches a store outside the flag, not even the status SQL.
    env.setenv("APPSELL_FINANCIAL_SELF_SERVICE_SELLER_IDS", "")
    auth = FakeAuth(seller_id=SELLER_ID, script=["seller_admin"])
    response = await get(auth)

    assert response.status_code == 200
    assert response.json() == {"self_service": False}
    assert len(auth.session.calls) == 1
    assert auth.capabilities == ["commerce.payout.read"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "require", "expected"),
    [
        ({"bound": True, "payout_ready": False, "channel_ready": False}, "", ("active", None)),
        (
            {"bound": False, "payout_ready": False, "channel_ready": True},
            "",
            ("available", None),
        ),
        (
            {"bound": False, "payout_ready": False, "channel_ready": False},
            "",
            ("blocked", "channel_unavailable"),
        ),
        (
            {"bound": False, "payout_ready": False, "channel_ready": True},
            "true",
            ("blocked", "payout_account_not_ready"),
        ),
        (
            {"bound": False, "payout_ready": True, "channel_ready": True},
            "true",
            ("available", None),
        ),
    ],
)
async def test_read_reports_what_activation_would_do(
    env: pytest.MonkeyPatch,
    row: dict[str, bool],
    require: str,
    expected: tuple[str, str | None],
) -> None:
    env.setenv("APPSELL_FINANCIAL_ACTIVATION_REQUIRES_PAYOUT_ACCOUNT", require)
    auth = FakeAuth(seller_id=SELLER_ID, script=["seller_admin", situation(**row)])
    response = await get(auth)

    assert response.status_code == 200
    assert response.json() == {
        "self_service": True,
        "status": expected[0],
        "reason": expected[1],
    }


@pytest.mark.asyncio
async def test_read_without_the_pair_is_blocked(env: pytest.MonkeyPatch) -> None:
    env.delenv("APPSELL_FINANCIAL_SHARED_OPERATOR_ID")
    env.delenv("APPSELL_FINANCIAL_SHARED_CATALOG_ITEM_ID")
    auth = FakeAuth(
        seller_id=SELLER_ID,
        script=["seller_owner", situation(bound=False, payout_ready=True, channel_ready=True)],
    )
    response = await get(auth)

    assert response.json()["reason"] == "unconfigured"


@pytest.mark.parametrize(
    "environ",
    [
        {"APPSELL_FINANCIAL_SELF_SERVICE_SELLER_IDS": "hub2you"},
        {"APPSELL_FINANCIAL_SHARED_OPERATOR_ID": str(OPERATOR)},
        {"APPSELL_FINANCIAL_ACTIVATION_REQUIRES_PAYOUT_ACCOUNT": "sim"},
    ],
)
def test_malformed_env_fails_closed(environ: dict[str, str]) -> None:
    with pytest.raises(FinancialActivationConfigError):
        financial_activation_config(environ)


@pytest.mark.asyncio
async def test_malformed_env_is_unconfigured_on_the_route(env: pytest.MonkeyPatch) -> None:
    env.setenv("APPSELL_FINANCIAL_SELF_SERVICE_SELLER_IDS", "hub2you")
    auth = FakeAuth(seller_id=SELLER_ID, script=[])
    response = await post(auth)

    assert response.status_code == 503
    assert auth.session.calls == []
