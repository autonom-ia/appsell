"""A checagem única de acesso (#558): um motivo por caso, e a mesma resposta das rotas.

Para cada motivo, o teste prepara o banco, pergunta à função e confere que a
biblioteca e o asset dizem a mesma coisa: a função nega exatamente quando eles
negam. Ela explica a negação; nunca libera o que eles bloqueiam.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from test_postgres_demo_learning import (
    ADMIN_URL,
    ISSUER,
    OTHER_USER,
    RUNTIME_URL,
    apply_context,
    identity_id,
    reset_once,
)

from appsell_api.reset_demo_tenant import load_manifest

pytestmark = pytest.mark.skipif(
    not (ADMIN_URL and RUNTIME_URL),
    reason="isolated PostgreSQL environment is required",
)


def _clean_grants() -> None:
    # O reset do demo não conhece o ledger de compras; cada caso começa sem
    # compra registrada para o par comprador e produto.
    case = ids()
    admin(
        "delete from app_private.entitlement_grant "
        "where seller_organization_id=%s and buyer_identity_user_id=%s",
        (case["seller"], case["buyer"]),
    )


@pytest.fixture(autouse=True)
def fresh_demo() -> Iterator[None]:
    manifest, manifest_sha = load_manifest()
    admin(
        "insert into identity_user("
        "id,identity_issuer,identity_sub,email_normalized,email_verified) "
        "values(%s,%s,'other-demo-user','other.demo@autonomia.site',true) "
        "on conflict do nothing",
        (OTHER_USER, ISSUER),
    )
    _clean_grants()
    reset_once(manifest, manifest_sha)
    yield
    _clean_grants()
    reset_once(manifest, manifest_sha)


def ids() -> dict[str, UUID]:
    manifest, _ = load_manifest()
    return {
        "seller": UUID(manifest["seller"]["id"]),
        "buyer": identity_id(manifest["personas"]["buyer"]["subject"]),
        "product": UUID(manifest["product"]["id"]),
        "entitlement": UUID(manifest["entitlement"]["id"]),
        "asset": UUID(manifest["lessons"][0]["asset_id"]),
    }


def admin(statement: str, parameters: tuple[Any, ...]) -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(statement, parameters)
        connection.commit()


def decision(actor: UUID, entitlement: UUID) -> tuple[bool, str, bool]:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, actor, "library.read")
        row = connection.execute(
            "select allowed,reason,update_pending from app_private.buyer_access_decision(%s,%s)",
            (actor, entitlement),
        ).fetchone()
    assert row is not None
    return (bool(row[0]), str(row[1]), bool(row[2]))


def library_has(actor: UUID, entitlement: UUID) -> bool:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, actor, "library.read")
        row = connection.execute("select app_private.read_buyer_library(%s)", (actor,)).fetchone()
    assert row is not None
    return any(item["entitlement_id"] == str(entitlement) for item in row[0])


def asset_opens(actor: UUID, entitlement: UUID, asset: UUID) -> bool:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, actor, "library.asset.read")
        try:
            connection.execute(
                "select * from app_private.authorize_learning_asset(%s,%s,%s)",
                (actor, entitlement, asset),
            ).fetchone()
        except psycopg.errors.InvalidAuthorizationSpecification:
            return False
    return True


def grant(status: str) -> None:
    case = ids()
    admin(
        "insert into app_private.entitlement_grant("
        "seller_organization_id,checkout_integration_id,external_purchase_id_sha256,"
        "buyer_identity_user_id,catalog_product_id,status,occurred_at"
        ") values(%s,%s,%s,%s,%s,%s,clock_timestamp())",
        (
            case["seller"],
            uuid4(),
            uuid4().hex + uuid4().hex,
            case["buyer"],
            case["product"],
            status,
        ),
    )


def revoke() -> None:
    case = ids()
    admin(
        "update buyer_entitlement set status='revoked' where seller_organization_id=%s and id=%s",
        (case["seller"], case["entitlement"]),
    )


def refunded() -> None:
    grant("refunded")
    revoke()


def refunded_but_another_purchase_active() -> None:
    # Estorno de uma compra com outra ativa: o fulfillment não revoga; se a
    # linha estiver revogada por outro motivo, o motivo não é o estorno.
    grant("refunded")
    grant("active")
    revoke()


def expired_by_date() -> None:
    case = ids()
    admin(
        "update buyer_entitlement set starts_at=clock_timestamp()-interval '2 days',"
        "expires_at=clock_timestamp()-interval '1 day' "
        "where seller_organization_id=%s and id=%s",
        (case["seller"], case["entitlement"]),
    )


def expired_by_status() -> None:
    case = ids()
    admin(
        "update buyer_entitlement set status='expired' where seller_organization_id=%s and id=%s",
        (case["seller"], case["entitlement"]),
    )


def not_started() -> None:
    case = ids()
    admin(
        "update buyer_entitlement set starts_at=clock_timestamp()+interval '1 day' "
        "where seller_organization_id=%s and id=%s",
        (case["seller"], case["entitlement"]),
    )


def seller_suspended() -> None:
    admin("update seller_organization set status='suspended' where id=%s", (ids()["seller"],))


def nothing() -> None:
    return None


CASES: list[tuple[str, Callable[[], None], bool, str]] = [
    ("active", nothing, True, "active"),
    ("refunded", refunded, False, "refunded"),
    ("revoked_with_other_active_purchase", refunded_but_another_purchase_active, False, "revoked"),
    ("revoked", revoke, False, "revoked"),
    ("expired_by_date", expired_by_date, False, "expired"),
    ("expired_by_status", expired_by_status, False, "expired"),
    ("not_started", not_started, False, "not_started"),
    ("seller_suspended", seller_suspended, False, "seller_unavailable"),
]


@pytest.mark.parametrize(("name", "prepare", "allowed", "reason"), CASES, ids=[c[0] for c in CASES])
def test_reason_matches_what_the_content_routes_decide(
    name: str, prepare: Callable[[], None], allowed: bool, reason: str
) -> None:
    case = ids()
    prepare()

    got = decision(case["buyer"], case["entitlement"])

    assert got[:2] == (allowed, reason), name
    assert library_has(case["buyer"], case["entitlement"]) is allowed
    assert asset_opens(case["buyer"], case["entitlement"], case["asset"]) is allowed


def test_other_buyer_and_unknown_entitlement_answer_the_same() -> None:
    case = ids()
    assert decision(OTHER_USER, case["entitlement"]) == (False, "not_found", False)
    assert decision(case["buyer"], uuid4()) == (False, "not_found", False)


def test_other_buyer_cannot_learn_the_reason_of_a_refunded_purchase() -> None:
    case = ids()
    refunded()
    assert decision(OTHER_USER, case["entitlement"]) == (False, "not_found", False)


def test_unpublished_product_is_product_unavailable() -> None:
    case = ids()
    admin(
        "update catalog_product set status='unpublished' where seller_organization_id=%s and id=%s",
        (case["seller"], case["product"]),
    )

    assert decision(case["buyer"], case["entitlement"])[:2] == (False, "product_unavailable")
    assert library_has(case["buyer"], case["entitlement"]) is False
    assert asset_opens(case["buyer"], case["entitlement"], case["asset"]) is False


def test_decision_requires_the_library_context() -> None:
    assert RUNTIME_URL
    case = ids()
    with psycopg.connect(RUNTIME_URL) as connection:
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select * from app_private.buyer_access_decision(%s,%s)",
                (case["buyer"], case["entitlement"]),
            )
        connection.rollback()
        apply_context(connection, case["buyer"], "library.read", case["seller"])
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select * from app_private.buyer_access_decision(%s,%s)",
                (case["buyer"], case["entitlement"]),
            )
        connection.rollback()
        # O envelope de um comprador não pergunta pelo de outro.
        apply_context(connection, OTHER_USER, "library.read")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select * from app_private.buyer_access_decision(%s,%s)",
                (case["buyer"], case["entitlement"]),
            )


def test_runtime_only_executes_the_function() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select has_function_privilege('appsell_runtime',"
            "'app_private.buyer_access_decision(uuid,uuid)','EXECUTE'),"
            "has_function_privilege('public',"
            "'app_private.buyer_access_decision(uuid,uuid)','EXECUTE'),"
            "app_private.schema_ready()"
        ).fetchone()
    assert row == (True, False, True)
