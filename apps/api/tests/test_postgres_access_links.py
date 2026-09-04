from __future__ import annotations

import hashlib
import hmac
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.environ.get("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY_HEX = os.environ.get("APPSELL_TEST_CONTEXT_KEY_HEX")

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY_HEX)),
    reason="isolated PostgreSQL environment is required",
)

SELLER_USER = UUID("62000000-0000-4000-8000-000000000001")
BUYER_COUNT = 60


def buyer_id(index: int) -> UUID:
    return UUID(f"63000000-0000-4000-8000-{index:012d}")


def sign_context(
    actor: UUID, seller: UUID | None, capability: str, issued: int, expires: int
) -> str:
    payload = f"{actor}:{seller or ''}:{capability}:{issued}:{expires}"
    return hmac.new(
        bytes.fromhex(CONTEXT_KEY_HEX or ""), payload.encode(), hashlib.sha256
    ).hexdigest()


def apply_context(
    connection: psycopg.Connection[tuple[object, ...]],
    actor: UUID,
    seller: UUID | None,
    capability: str,
) -> None:
    issued = int(time.time())
    expires = issued + 60
    connection.execute(
        "select app_private.apply_request_context(%s, %s, %s, %s, %s, %s)",
        (
            actor,
            seller,
            capability,
            issued,
            expires,
            sign_context(actor, seller, capability, issued, expires),
        ),
    )


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture(scope="module", autouse=True)
def seed_identities() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            """
            insert into identity_user (
              id, identity_issuer, identity_sub, email_normalized, email_verified
            ) values (%s, 'https://identity.accesslink.test', 'access-seller',
              'access-seller@example.test', true)
            on conflict (id) do nothing
            """,
            (SELLER_USER,),
        )
        for index in range(BUYER_COUNT):
            connection.execute(
                """
                insert into identity_user (
                  id, identity_issuer, identity_sub, email_normalized, email_verified
                ) values (%s, 'https://identity.accesslink.test', %s, %s, true)
                on conflict (id) do nothing
                """,
                (
                    buyer_id(index),
                    f"access-buyer-{index}",
                    f"access-buyer-{index}@example.test",
                ),
            )
        connection.commit()


def create_graph() -> tuple[UUID, UUID, UUID]:
    assert ADMIN_URL
    seller_id, product_id, course_id = uuid4(), uuid4(), uuid4()
    suffix = uuid4().hex[:12]
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "insert into seller_organization (id, slug, display_name, status) "
            "values (%s, %s, %s, 'active')",
            (seller_id, f"accesslink-{suffix}", f"Access {suffix}"),
        )
        connection.execute(
            "insert into seller_membership "
            "(seller_organization_id, identity_user_id, role, status) "
            "values (%s, %s, 'seller_owner', 'active')",
            (seller_id, SELLER_USER),
        )
        connection.execute(
            "insert into catalog_product (seller_organization_id, id, slug, title, description) "
            "values (%s, %s, %s, 'Produto Base', 'Descrição Base')",
            (seller_id, product_id, f"product-{suffix}"),
        )
        connection.execute(
            "insert into course (seller_organization_id, id, title, description) "
            "values (%s, %s, 'Curso Base', 'Conteúdo Base')",
            (seller_id, course_id),
        )
        connection.execute(
            "insert into product_course "
            "(seller_organization_id, catalog_product_id, course_id, position) "
            "values (%s, %s, %s, 0)",
            (seller_id, product_id, course_id),
        )
        connection.commit()
    return seller_id, product_id, course_id


def bind_payout_account(seller_id: UUID, *, enabled: bool = True) -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            """
            insert into seller_payout_account (
              seller_organization_id, provider, provider_account_id,
              charges_enabled, payouts_enabled, details_submitted, disabled_reason
            ) values (%s, 'stripe', %s, %s, %s, %s, %s)
            """,
            (
                seller_id,
                f"acct_{uuid4().hex[:16]}",
                enabled,
                enabled,
                enabled,
                None if enabled else "requirements.past_due",
            ),
        )
        connection.commit()


def save_draft(seller_id: UUID, product_id: UUID, course_id: UUID, price: int = 9700) -> UUID:
    """Create the offer through the same atomic draft path the editor uses."""
    assert RUNTIME_URL
    offer_id = uuid4()
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, SELLER_USER, seller_id, "catalog.draft.update")
        connection.execute(
            """
            select offer_id
            from app_private.update_product_course_offer_draft(
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                SELLER_USER,
                seller_id,
                product_id,
                f"edited-{product_id.hex[:12]}",
                course_id,
                offer_id,
                "Produto Editado",
                "Descrição editada",
                "Curso Editado",
                "Oferta Principal",
                "Resumo da oferta",
                price,
                "BRL",
                "one_time",
                1,
                1,
                0,
                uuid4(),
                digest(f"draft:{product_id}"),
                uuid4(),
            ),
        ).fetchone()
        connection.commit()
    return offer_id


def publish_with_access(
    seller_id: UUID,
    product_id: UUID,
    course_id: UUID,
    offer_id: UUID,
    *,
    access_mode: str,
    product_version: int = 2,
    course_version: int = 2,
    offer_version: int = 1,
    key: UUID | None = None,
) -> tuple[Any, ...]:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, SELLER_USER, seller_id, "catalog.publication.create")
        row = connection.execute(
            """
            select publication_id, publication_version, product_version, source_sha256,
                   offer_status, offer_version, created
            from app_private.publish_product_with_access(
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                SELLER_USER,
                seller_id,
                product_id,
                offer_id,
                product_version,
                offer_version,
                Jsonb({str(course_id): course_version}),
                access_mode,
                key or uuid4(),
                digest(f"publish:{product_id}:{access_mode}"),
                uuid4(),
            ),
        ).fetchone()
        assert row is not None
        connection.commit()
    return row


def create_link(
    seller_id: UUID,
    product_id: UUID,
    *,
    token: str,
    label: str = "Turma de teste",
    max_uses: int | None = 30,
    expires_at: datetime | None = None,
    key: UUID | None = None,
    body: str | None = None,
) -> UUID:
    assert RUNTIME_URL
    token_digest = digest(token)
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, SELLER_USER, seller_id, "catalog.access_link.create")
        row = connection.execute(
            """
            select access_link_id
            from app_private.create_product_access_link(
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                SELLER_USER,
                seller_id,
                product_id,
                label,
                token_digest,
                token_digest[:8],
                max_uses,
                expires_at,
                key or uuid4(),
                digest(body or f"link:{product_id}:{label}"),
                uuid4(),
            ),
        ).fetchone()
        assert row is not None
        connection.commit()
    assert isinstance(row[0], UUID)
    return row[0]


def redeem(
    token: str, buyer: UUID, key: UUID | None = None, body: str | None = None
) -> tuple[Any, ...]:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, buyer, None, "library.enroll")
        row = connection.execute(
            """
            select entitlement_id, seller_organization_id, catalog_product_id,
                   created, seat_consumed, seats_remaining
            from app_private.redeem_product_access_link(%s, %s, %s, %s, %s)
            """,
            (
                buyer,
                digest(token),
                key or uuid4(),
                digest(body or f"redeem:{buyer}:{uuid4()}"),
                uuid4(),
            ),
        ).fetchone()
        assert row is not None
        connection.commit()
    return row


def read_product_status(seller_id: UUID, product_id: UUID) -> tuple[Any, ...] | None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        return connection.execute(
            "select status, published_snapshot_id, version from catalog_product "
            "where seller_organization_id = %s and id = %s",
            (seller_id, product_id),
        ).fetchone()


def read_offer_status(seller_id: UUID, offer_id: UUID) -> tuple[Any, ...] | None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        return connection.execute(
            "select status, version from offer where seller_organization_id = %s and id = %s",
            (seller_id, offer_id),
        ).fetchone()


def count_publications(seller_id: UUID, product_id: UUID) -> int:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select count(*) from publication "
            "where seller_organization_id = %s and catalog_product_id = %s",
            (seller_id, product_id),
        ).fetchone()
    assert row is not None
    return int(row[0])


def audit_actions(seller_id: UUID) -> list[str]:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        rows = connection.execute(
            "select action from audit_event where seller_organization_id = %s order by created_at",
            (seller_id,),
        ).fetchall()
    return [str(row[0]) for row in rows]


def published_free_product() -> tuple[UUID, UUID]:
    seller_id, product_id, course_id = create_graph()
    offer_id = save_draft(seller_id, product_id, course_id)
    publish_with_access(seller_id, product_id, course_id, offer_id, access_mode="free")
    return seller_id, product_id


def test_paid_publication_activates_the_offer_in_the_same_transaction() -> None:
    """A1.1 — publish and activation are inseparable.

    The seller ends the call with a published catalog AND an active offer, from a
    single write. Nothing in between is observable, because there is no second
    transaction to observe.
    """
    seller_id, product_id, course_id = create_graph()
    bind_payout_account(seller_id)
    offer_id = save_draft(seller_id, product_id, course_id)

    row = publish_with_access(seller_id, product_id, course_id, offer_id, access_mode="paid")

    assert row[4] == "active"
    assert bool(row[6]) is True
    product = read_product_status(seller_id, product_id)
    assert product is not None
    assert product[0] == "published"
    assert product[1] is not None
    offer = read_offer_status(seller_id, offer_id)
    assert offer is not None
    assert offer[0] == "active"


def test_paid_publication_without_a_payout_account_publishes_nothing() -> None:
    """A1.1 and A1.3 — the failure path leaves no half-finished state.

    This is the injected failure the plan asks for: the sale is refused, and the
    catalog must NOT be published as a side effect. A seller who cannot be paid
    must not discover later that the product went live for free.
    """
    seller_id, product_id, course_id = create_graph()
    offer_id = save_draft(seller_id, product_id, course_id)

    with pytest.raises(psycopg.errors.Error) as failure:
        publish_with_access(seller_id, product_id, course_id, offer_id, access_mode="paid")
    assert failure.value.sqlstate == "P4223"

    product = read_product_status(seller_id, product_id)
    assert product is not None
    assert product[0] == "draft"
    assert product[1] is None
    assert count_publications(seller_id, product_id) == 0
    offer = read_offer_status(seller_id, offer_id)
    assert offer is not None
    assert offer[0] == "draft"


def test_paid_publication_without_a_positive_price_publishes_nothing() -> None:
    """A1.1 — second injected failure, same guarantee."""
    seller_id, product_id, course_id = create_graph()
    bind_payout_account(seller_id)
    offer_id = save_draft(seller_id, product_id, course_id, price=0)

    with pytest.raises(psycopg.errors.Error) as failure:
        publish_with_access(seller_id, product_id, course_id, offer_id, access_mode="paid")
    assert failure.value.sqlstate == "P4227"
    assert count_publications(seller_id, product_id) == 0


def test_free_publication_keeps_the_offer_in_draft() -> None:
    """A1.2 — free access is a named outcome, not a pending state."""
    seller_id, product_id, course_id = create_graph()
    offer_id = save_draft(seller_id, product_id, course_id)

    row = publish_with_access(seller_id, product_id, course_id, offer_id, access_mode="free")

    assert row[4] == "draft"
    product = read_product_status(seller_id, product_id)
    assert product is not None
    assert product[0] == "published"


def test_paid_publication_closes_the_free_enrolment_window() -> None:
    """The vulnerability this migration exists to fix.

    Before it, a product destined to be sold spent the gap between publish and
    activate publicly enrollable for free. After it, the first thing a visitor can
    attempt is already refused.
    """
    seller_id, product_id, course_id = create_graph()
    bind_payout_account(seller_id)
    offer_id = save_draft(seller_id, product_id, course_id)
    publish_with_access(seller_id, product_id, course_id, offer_id, access_mode="paid")

    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, buyer_id(0), None, "library.enroll")
        with pytest.raises(psycopg.errors.Error) as failure:
            connection.execute(
                "select entitlement_id from app_private.grant_public_product_access("
                "%s, %s, %s, %s, %s, %s)",
                (buyer_id(0), seller_id, product_id, uuid4(), digest("free-attempt"), uuid4()),
            ).fetchone()
    assert failure.value.sqlstate == "P4222"


def test_publication_replay_does_not_create_a_second_version() -> None:
    """A1.4 — a retry is a replay, never a second publication."""
    seller_id, product_id, course_id = create_graph()
    offer_id = save_draft(seller_id, product_id, course_id)
    key = uuid4()

    first = publish_with_access(
        seller_id, product_id, course_id, offer_id, access_mode="free", key=key
    )
    second = publish_with_access(
        seller_id, product_id, course_id, offer_id, access_mode="free", key=key
    )

    assert bool(first[6]) is True
    assert bool(second[6]) is False
    assert first[0] == second[0]
    assert count_publications(seller_id, product_id) == 1


def test_concurrent_redeemers_never_exceed_the_seat_budget() -> None:
    """A1.5 — fifty people arrive at once at a thirty seat link.

    Sequential counting would pass a naive implementation, so this has to be a
    real race. Exactly thirty entitlements may exist afterwards, and the twenty
    losers must be told the link is full rather than silently admitted.
    """
    seller_id, product_id = published_free_product()
    token = f"seat-race-{uuid4().hex}"
    create_link(seller_id, product_id, token=token, max_uses=30)

    def attempt(index: int) -> str:
        try:
            redeem(token, buyer_id(index))
        except psycopg.errors.Error as error:
            # A connection level failure has no sqlstate. Surfacing it by name
            # keeps a future failure from looking like a seat accounting bug.
            return str(error.sqlstate) if error.sqlstate else f"driver:{type(error).__name__}"
        return "granted"

    # The test database runs with max_connections=24, so the pool stays well
    # under it. Fifty attempts through eight workers still contend for the same
    # row lock, which is what the seat budget depends on.
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(attempt, range(50)))

    assert [outcome for outcome in outcomes if outcome.startswith("driver:")] == []
    assert outcomes.count("granted") == 30
    assert outcomes.count("P4224") == 20

    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        redemptions = connection.execute(
            "select count(*) from product_access_link_redemption where seller_organization_id = %s",
            (seller_id,),
        ).fetchone()
        entitlements = connection.execute(
            "select count(*) from buyer_entitlement where seller_organization_id = %s",
            (seller_id,),
        ).fetchone()
    assert redemptions is not None and int(redemptions[0]) == 30
    assert entitlements is not None and int(entitlements[0]) == 30


def test_the_same_person_never_consumes_a_second_seat() -> None:
    """A1.6 — re-entry is free.

    Someone who reopens the link, or clicks it on a second device, must not eat
    another seat. A thirty seat class would otherwise be exhausted by a handful of
    people refreshing the page.
    """
    seller_id, product_id = published_free_product()
    token = f"reentry-{uuid4().hex}"
    create_link(seller_id, product_id, token=token, max_uses=3)

    results = [redeem(token, buyer_id(1)) for _ in range(5)]

    assert bool(results[0][4]) is True
    assert all(bool(row[4]) is False for row in results[1:])
    assert all(int(row[5]) == 2 for row in results)
    assert len({row[0] for row in results}) == 1


def test_revocation_blocks_new_entry_and_preserves_existing_access() -> None:
    """A1.7 — revoking closes the door without evicting who is already inside."""
    seller_id, product_id = published_free_product()
    token = f"revoke-{uuid4().hex}"
    link_id = create_link(seller_id, product_id, token=token, max_uses=10)
    admitted = redeem(token, buyer_id(2))

    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, SELLER_USER, seller_id, "catalog.access_link.update")
        row = connection.execute(
            "select changed from app_private.revoke_product_access_link(%s,%s,%s,%s,%s,%s,%s)",
            (SELLER_USER, seller_id, product_id, link_id, uuid4(), digest("revoke"), uuid4()),
        ).fetchone()
        connection.commit()
    assert row is not None and bool(row[0]) is True

    with pytest.raises(psycopg.errors.Error) as failure:
        redeem(token, buyer_id(3))
    assert failure.value.sqlstate == "P4225"

    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        still_active = connection.execute(
            "select status from buyer_entitlement where id = %s",
            (admitted[0],),
        ).fetchone()
    assert still_active is not None and still_active[0] == "active"


def test_expired_links_refuse_entry_even_with_seats_left() -> None:
    """A1.8 — the clock wins over the counter."""
    seller_id, product_id = published_free_product()
    token = f"expiry-{uuid4().hex}"
    link_id = create_link(
        seller_id,
        product_id,
        token=token,
        max_uses=10,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "update product_access_link set expires_at = now() - interval '1 minute' "
            "where seller_organization_id = %s and id = %s",
            (seller_id, link_id),
        )
        connection.commit()

    with pytest.raises(psycopg.errors.Error) as failure:
        redeem(token, buyer_id(4))
    assert failure.value.sqlstate == "P4225"


def test_rotation_keeps_the_seats_and_kills_the_previous_address() -> None:
    """A1.9 — losing the link costs the address, not the class."""
    seller_id, product_id = published_free_product()
    token = f"rotate-{uuid4().hex}"
    link_id = create_link(seller_id, product_id, token=token, max_uses=5)
    redeem(token, buyer_id(5))

    replacement = f"rotated-{uuid4().hex}"
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, SELLER_USER, seller_id, "catalog.access_link.update")
        row = connection.execute(
            "select redeemed_count, max_uses "
            "from app_private.rotate_product_access_link(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                SELLER_USER,
                seller_id,
                product_id,
                link_id,
                digest(replacement),
                digest(replacement)[:8],
                uuid4(),
                digest("rotate"),
                uuid4(),
            ),
        ).fetchone()
        connection.commit()
    assert row is not None
    assert int(row[0]) == 1
    assert int(row[1]) == 5

    with pytest.raises(psycopg.errors.Error) as failure:
        redeem(token, buyer_id(6))
    assert failure.value.sqlstate == "P4225"

    granted = redeem(replacement, buyer_id(6))
    assert bool(granted[4]) is True
    assert int(granted[5]) == 3


def test_unknown_and_revoked_links_are_indistinguishable() -> None:
    """A1.11 — probing must not reveal whether a token exists."""
    seller_id, product_id = published_free_product()
    token = f"probe-{uuid4().hex}"
    link_id = create_link(seller_id, product_id, token=token, max_uses=1)

    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, SELLER_USER, seller_id, "catalog.access_link.update")
        connection.execute(
            "select changed from app_private.revoke_product_access_link(%s,%s,%s,%s,%s,%s,%s)",
            (SELLER_USER, seller_id, product_id, link_id, uuid4(), digest("revoke"), uuid4()),
        ).fetchone()
        connection.commit()

    with pytest.raises(psycopg.errors.Error) as revoked:
        redeem(token, buyer_id(7))
    with pytest.raises(psycopg.errors.Error) as unknown:
        redeem(f"never-existed-{uuid4().hex}", buyer_id(7))

    assert revoked.value.sqlstate == unknown.value.sqlstate == "P4225"


def test_every_link_operation_leaves_an_audit_trail() -> None:
    """A1.12 — creation, revocation, rotation and redemption are all accountable."""
    seller_id, product_id = published_free_product()
    token = f"audit-{uuid4().hex}"
    link_id = create_link(seller_id, product_id, token=token, max_uses=4)
    redeem(token, buyer_id(8))

    replacement = f"audit-rotated-{uuid4().hex}"
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, SELLER_USER, seller_id, "catalog.access_link.update")
        connection.execute(
            "select access_link_id "
            "from app_private.rotate_product_access_link(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                SELLER_USER,
                seller_id,
                product_id,
                link_id,
                digest(replacement),
                digest(replacement)[:8],
                uuid4(),
                digest("rotate"),
                uuid4(),
            ),
        ).fetchone()
        connection.execute(
            "select changed from app_private.revoke_product_access_link(%s,%s,%s,%s,%s,%s,%s)",
            (SELLER_USER, seller_id, product_id, link_id, uuid4(), digest("revoke"), uuid4()),
        ).fetchone()
        connection.commit()

    actions = audit_actions(seller_id)
    assert "catalog.access_link_created" in actions
    assert "commerce.access_link_redeemed" in actions
    assert "catalog.access_link_rotated" in actions
    assert "catalog.access_link_revoked" in actions


def test_the_raw_token_is_never_stored() -> None:
    """A1.9 and A1.10 — a database dump must not hand out access."""
    seller_id, product_id = published_free_product()
    token = f"invitation-{uuid4().hex}"
    create_link(seller_id, product_id, token=token)

    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select token_sha256, token_prefix from product_access_link "
            "where seller_organization_id = %s",
            (seller_id,),
        ).fetchone()
    assert row is not None
    assert row[0] == digest(token)
    assert token not in str(row[0])
    assert row[1] == digest(token)[:8]


def test_a_seller_cannot_reach_another_tenant_link() -> None:
    """Cross-tenant isolation on the seat budget itself."""
    owner_seller, owner_product = published_free_product()
    other_seller, other_product, _ = create_graph()
    token = f"tenant-{uuid4().hex}"
    link_id = create_link(owner_seller, owner_product, token=token, max_uses=2)

    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, SELLER_USER, other_seller, "catalog.access_link.update")
        with pytest.raises(psycopg.errors.Error) as failure:
            connection.execute(
                "select changed from app_private.revoke_product_access_link(%s,%s,%s,%s,%s,%s,%s)",
                (
                    SELLER_USER,
                    other_seller,
                    other_product,
                    link_id,
                    uuid4(),
                    digest("cross"),
                    uuid4(),
                ),
            ).fetchone()
    assert failure.value.sqlstate == "P4225"


def test_a_sold_product_refuses_new_access_links() -> None:
    """A link that could never be redeemed must not be handed out.

    Redemption grants free access, and a sold product refuses that, so minting an
    invitation for one would only produce a dead address in someone's hands.
    """
    seller_id, product_id, course_id = create_graph()
    bind_payout_account(seller_id)
    offer_id = save_draft(seller_id, product_id, course_id)
    publish_with_access(seller_id, product_id, course_id, offer_id, access_mode="paid")

    with pytest.raises(psycopg.errors.Error) as failure:
        create_link(seller_id, product_id, token=f"sold-{uuid4().hex}", max_uses=5)
    assert failure.value.sqlstate == "P4226"


def test_creating_a_link_twice_with_one_key_yields_one_link() -> None:
    """A retry after a lost response must replay, not mint a second budget.

    Without this, a seller who reloads after a timeout ends up with two links of
    thirty seats each and hands out sixty.
    """
    seller_id, product_id = published_free_product()
    key = uuid4()
    token = f"replay-{uuid4().hex}"
    first = create_link(seller_id, product_id, token=token, max_uses=10, key=key, body="fixed")
    second = create_link(seller_id, product_id, token=token, max_uses=10, key=key, body="fixed")

    assert first == second
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        count = connection.execute(
            "select count(*) from product_access_link where seller_organization_id = %s",
            (seller_id,),
        ).fetchone()
    assert count is not None and int(count[0]) == 1


def test_reusing_a_key_for_a_different_link_is_refused() -> None:
    """The key identifies one operation, so it cannot be recycled."""
    seller_id, product_id = published_free_product()
    key = uuid4()
    create_link(seller_id, product_id, token=f"a-{uuid4().hex}", max_uses=5, key=key, body="one")

    with pytest.raises(psycopg.errors.Error) as failure:
        create_link(
            seller_id, product_id, token=f"b-{uuid4().hex}", max_uses=5, key=key, body="two"
        )
    assert failure.value.sqlstate == "23505"


def test_redemption_retried_after_revocation_still_returns_the_seat() -> None:
    """Revoking closes the door; it does not evict who is already inside.

    A buyer whose first response was lost retries and must get their access back,
    not a refusal that looks like they were thrown out.
    """
    seller_id, product_id = published_free_product()
    token = f"retry-{uuid4().hex}"
    link_id = create_link(seller_id, product_id, token=token, max_uses=5)
    granted = redeem(token, buyer_id(9))

    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, SELLER_USER, seller_id, "catalog.access_link.update")
        connection.execute(
            "select changed from app_private.revoke_product_access_link(%s,%s,%s,%s,%s,%s,%s)",
            (SELLER_USER, seller_id, product_id, link_id, uuid4(), digest("revoke"), uuid4()),
        ).fetchone()
        connection.commit()

    retried = redeem(token, buyer_id(9))
    assert retried[0] == granted[0]
    assert bool(retried[4]) is False


def test_paid_publication_replay_survives_a_payout_account_going_bad() -> None:
    """A replay must not re-litigate a decision the first call already committed."""
    seller_id, product_id, course_id = create_graph()
    bind_payout_account(seller_id)
    offer_id = save_draft(seller_id, product_id, course_id)
    key = uuid4()
    first = publish_with_access(
        seller_id, product_id, course_id, offer_id, access_mode="paid", key=key
    )
    assert first[4] == "active"

    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "update seller_payout_account set charges_enabled = false, "
            "disabled_reason = 'requirements.past_due' where seller_organization_id = %s",
            (seller_id,),
        )
        connection.commit()

    replay = publish_with_access(
        seller_id, product_id, course_id, offer_id, access_mode="paid", key=key
    )
    assert bool(replay[6]) is False
    assert replay[0] == first[0]


def test_a_seller_cannot_list_another_tenant_links() -> None:
    """Tenant isolation on the read path, not only on the mutations."""
    owner_seller, owner_product = published_free_product()
    other_seller, other_product, _ = create_graph()
    create_link(owner_seller, owner_product, token=f"read-{uuid4().hex}", max_uses=2)

    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, SELLER_USER, other_seller, "catalog.access_link.read")
        rows = connection.execute(
            "select access_link_id from app_private.list_product_access_links(%s,%s,%s)",
            (SELLER_USER, other_seller, other_product),
        ).fetchall()
    assert rows == []


def test_redeeming_a_class_link_enqueues_one_access_email_per_person() -> None:
    # O link da turma é a outra porta de entrada: quem entra recebe o mesmo
    # "acesso liberado" da compra, uma vez só — repetir o link não manda outro,
    # e a segunda pessoa recebe o dela.
    assert ADMIN_URL
    seller_id, product_id = published_free_product()
    token = uuid4().hex + uuid4().hex
    link_id = create_link(seller_id, product_id, token=token, max_uses=5)
    redeem(token, buyer_id(0))
    redeem(token, buyer_id(0))
    redeem(token, buyer_id(1))
    with psycopg.connect(ADMIN_URL) as connection:
        rows = connection.execute(
            "select kind,recipient_identity_user_id,recipient_email,payload,status "
            "from app_private.email_outbox where seller_organization_id=%s "
            "and dedupe_key like %s order by created_at",
            (seller_id, f"access_link:{link_id}:%"),
        ).fetchall()
        emails: dict[UUID, str] = dict(
            connection.execute(
                "select id,email_normalized from identity_user where id in (%s,%s)",
                (buyer_id(0), buyer_id(1)),
            ).fetchall()
        )
    assert [(r[0], r[1], r[4]) for r in rows] == [
        ("access_link_redeemed", buyer_id(0), "pending"),
        ("access_link_redeemed", buyer_id(1), "pending"),
    ]
    for _kind, buyer, recipient, payload, _status in rows:
        assert recipient == emails[buyer]
        assert payload["product_title"] == "Produto Editado"  # save_draft renomeia
        assert UUID(str(payload["access_link_id"])) == link_id
        assert UUID(str(payload["catalog_product_id"])) == product_id
    # Quem já tinha o curso e entra por OUTRO link consome a vaga, mas não
    # ouve que "o link liberou o curso" — não liberou.
    outro_token = uuid4().hex + uuid4().hex
    outro_link = create_link(seller_id, product_id, token=outro_token, max_uses=5)
    assert redeem(outro_token, buyer_id(0))[3] is False  # created
    with psycopg.connect(ADMIN_URL) as connection:
        repetidos = connection.execute(
            "select count(*) from app_private.email_outbox where seller_organization_id=%s "
            "and dedupe_key=%s",
            (seller_id, f"access_link:{outro_link}:{buyer_id(0)}"),
        ).fetchone()
    assert repetidos == (0,)


def test_reactivating_a_revoked_entitlement_sends_the_email_again() -> None:
    # Acesso revogado que o link devolve É liberação de acesso: a linha da
    # entitlement já existe (created=false), mas a pessoa perdeu e reganhou o
    # curso — e precisa ouvir onde ele está.
    assert ADMIN_URL
    seller_id, product_id = published_free_product()
    primeiro = uuid4().hex + uuid4().hex
    create_link(seller_id, product_id, token=primeiro, max_uses=5)
    redeem(primeiro, buyer_id(2))
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "update buyer_entitlement set status='revoked' where seller_organization_id=%s "
            "and buyer_identity_user_id=%s and catalog_product_id=%s",
            (seller_id, buyer_id(2), product_id),
        )
        connection.commit()
    segundo = uuid4().hex + uuid4().hex
    outro_link = create_link(seller_id, product_id, token=segundo, max_uses=5)
    assert redeem(segundo, buyer_id(2))[3] is False  # a linha não é nova
    with psycopg.connect(ADMIN_URL) as connection:
        linhas = connection.execute(
            "select count(*) from app_private.email_outbox where seller_organization_id=%s "
            "and dedupe_key=%s",
            (seller_id, f"access_link:{outro_link}:{buyer_id(2)}"),
        ).fetchone()
    assert linhas == (1,)


def test_expired_entitlement_still_active_by_status_gets_the_email_again() -> None:
    # 'active' com validade vencida não dá acesso nenhum: as políticas olham a
    # validade. Reativar por link é liberação de acesso e precisa avisar.
    assert ADMIN_URL
    seller_id, product_id = published_free_product()
    primeiro = uuid4().hex + uuid4().hex
    create_link(seller_id, product_id, token=primeiro, max_uses=5)
    redeem(primeiro, buyer_id(3))
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "update buyer_entitlement set starts_at=clock_timestamp()-interval '2 days',"
            "expires_at=clock_timestamp()-interval '1 day' "
            "where seller_organization_id=%s and buyer_identity_user_id=%s "
            "and catalog_product_id=%s",
            (seller_id, buyer_id(3), product_id),
        )
        connection.commit()
    segundo = uuid4().hex + uuid4().hex
    outro_link = create_link(seller_id, product_id, token=segundo, max_uses=5)
    redeem(segundo, buyer_id(3))
    with psycopg.connect(ADMIN_URL) as connection:
        linhas = connection.execute(
            "select count(*) from app_private.email_outbox where seller_organization_id=%s "
            "and dedupe_key=%s",
            (seller_id, f"access_link:{outro_link}:{buyer_id(3)}"),
        ).fetchone()
    assert linhas == (1,)


def test_entitlement_with_a_future_start_also_gets_the_email() -> None:
    # 'active' que ainda não começou não dá acesso — as políticas exigem
    # starts_at no passado. O link antecipa o início: é liberação de acesso.
    assert ADMIN_URL
    seller_id, product_id = published_free_product()
    primeiro = uuid4().hex + uuid4().hex
    create_link(seller_id, product_id, token=primeiro, max_uses=5)
    redeem(primeiro, buyer_id(4))
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "update buyer_entitlement set starts_at=clock_timestamp()+interval '7 days' "
            "where seller_organization_id=%s and buyer_identity_user_id=%s "
            "and catalog_product_id=%s",
            (seller_id, buyer_id(4), product_id),
        )
        connection.commit()
    segundo = uuid4().hex + uuid4().hex
    outro_link = create_link(seller_id, product_id, token=segundo, max_uses=5)
    redeem(segundo, buyer_id(4))
    with psycopg.connect(ADMIN_URL) as connection:
        linhas = connection.execute(
            "select count(*) from app_private.email_outbox where seller_organization_id=%s "
            "and dedupe_key=%s",
            (seller_id, f"access_link:{outro_link}:{buyer_id(4)}"),
        ).fetchone()
    assert linhas == (1,)


def _acrescenta_aula(seller_id: UUID, course_id: UUID, titulo: str) -> UUID:
    """Acrescenta uma aula ao rascunho do curso e avanca a versao do rascunho."""
    assert ADMIN_URL
    lesson_id = uuid4()
    with psycopg.connect(ADMIN_URL) as connection:
        modulo = connection.execute(
            "select id from public.course_module"
            " where seller_organization_id=%s and course_id=%s order by position limit 1",
            (seller_id, course_id),
        ).fetchone()
        if modulo is None:
            module_id = uuid4()
            connection.execute(
                "insert into public.course_module"
                " (seller_organization_id, id, course_id, title, status, position)"
                " values (%s,%s,%s,'Modulo Base','active',0)",
                (seller_id, module_id, course_id),
            )
        else:
            module_id = UUID(str(modulo[0]))
        connection.execute(
            "insert into public.lesson"
            " (seller_organization_id, id, course_id, module_id, title, position)"
            " select %s,%s,%s,%s,%s,coalesce(max(existente.position),-1)+1"
            "   from public.lesson existente"
            "  where existente.seller_organization_id=%s and existente.module_id=%s",
            (seller_id, lesson_id, course_id, module_id, titulo, seller_id, module_id),
        )
        _avanca_rascunho(connection, seller_id, course_id)
        connection.commit()
    return lesson_id


def _arquiva_aula(seller_id: UUID, course_id: UUID, lesson_id: UUID) -> None:
    """Tira a aula do rascunho do curso e avanca a versao do rascunho."""
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "update public.lesson set status='archived', updated_at=clock_timestamp()"
            " where seller_organization_id=%s and id=%s",
            (seller_id, lesson_id),
        )
        _avanca_rascunho(connection, seller_id, course_id)
        connection.commit()


def _avanca_rascunho(connection: Any, seller_id: UUID, course_id: UUID) -> None:
    connection.execute(
        "update public.course set draft_version=draft_version+1, updated_at=clock_timestamp()"
        " where seller_organization_id=%s and id=%s",
        (seller_id, course_id),
    )


def _acesso_do_comprador(seller_id: UUID, product_id: UUID, comprador: UUID) -> tuple[Any, ...]:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        linha = connection.execute(
            """
            select be.id, be.publication_id, p.version
              from public.buyer_entitlement be
              join public.publication p on p.id = be.publication_id
             where be.seller_organization_id = %s
               and be.catalog_product_id = %s
               and be.buyer_identity_user_id = %s
            """,
            (seller_id, product_id, comprador),
        ).fetchone()
    assert linha is not None, "o comprador não tem acesso"
    return linha


def _da_acesso(seller_id: UUID, product_id: UUID, comprador: UUID) -> UUID:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, comprador, None, "library.enroll")
        linha = connection.execute(
            "select entitlement_id from app_private.grant_public_product_access(%s,%s,%s,%s,%s,%s)",
            (comprador, seller_id, product_id, uuid4(), digest(f"acesso:{comprador}"), uuid4()),
        ).fetchone()
        connection.commit()
    assert linha is not None
    return UUID(str(linha[0]))


def test_quem_ja_comprou_recebe_a_publicacao_nova() -> None:
    """O material acrescentado depois da compra chega a quem já pagou.

    Antes disto o acesso ficava congelado na publicação que existia no momento
    da compra. Medido em produção em 04/09/2026: o produto tinha três
    publicações e **nenhum comprador na mais nova**.
    """
    seller_id, product_id, course_id = create_graph()
    offer_id = save_draft(seller_id, product_id, course_id)
    publish_with_access(seller_id, product_id, course_id, offer_id, access_mode="free")

    comprador = buyer_id(7)
    _da_acesso(seller_id, product_id, comprador)
    _, publicacao_da_compra, versao_da_compra = _acesso_do_comprador(
        seller_id, product_id, comprador
    )

    _acrescenta_aula(seller_id, course_id, "Aula nova")
    publish_with_access(
        seller_id,
        product_id,
        course_id,
        offer_id,
        access_mode="free",
        product_version=3,
        course_version=3,
    )

    _, publicacao_agora, versao_agora = _acesso_do_comprador(seller_id, product_id, comprador)
    assert publicacao_agora != publicacao_da_compra, "o acesso não avançou"
    assert versao_agora == versao_da_compra + 1


def test_o_avanco_para_quando_a_publicacao_nova_tira_alguma_coisa() -> None:
    """A trava que o congelamento existia para dar continua valendo.

    O vendedor nao tira conteudo de quem pagou. Se a publicacao nova nao contem
    tudo o que a anterior tinha, ninguem avanca, e a decisao passa a ser do
    aluno em vez de um efeito colateral de publicar.
    """
    seller_id, product_id, course_id = create_graph()
    offer_id = save_draft(seller_id, product_id, course_id)
    _acrescenta_aula(seller_id, course_id, "Aula que fica")
    aula_removida = _acrescenta_aula(seller_id, course_id, "Aula que sai")
    publish_with_access(
        seller_id,
        product_id,
        course_id,
        offer_id,
        access_mode="free",
        course_version=4,
    )

    comprador = buyer_id(8)
    _da_acesso(seller_id, product_id, comprador)
    _, publicacao_da_compra, _ = _acesso_do_comprador(seller_id, product_id, comprador)

    _arquiva_aula(seller_id, course_id, aula_removida)
    publish_with_access(
        seller_id,
        product_id,
        course_id,
        offer_id,
        access_mode="free",
        product_version=3,
        course_version=5,
    )

    _, publicacao_agora, _ = _acesso_do_comprador(seller_id, product_id, comprador)
    assert publicacao_agora == publicacao_da_compra, (
        "o acesso avancou para uma publicacao que perdeu conteudo"
    )


def _estado_do_curso(seller_id: UUID, comprador: UUID) -> tuple[Any, ...]:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        linha = connection.execute(
            "select estado.publication_id, estado.total_lessons, estado.completed_lessons,"
            "       estado.progress_percent"
            "  from public.course_learning_state estado"
            " where estado.seller_organization_id=%s and estado.buyer_identity_user_id=%s",
            (seller_id, comprador),
        ).fetchone()
    assert linha is not None, "o comprador nao tem estado de curso"
    return linha


def test_o_progresso_do_aluno_sobrevive_ao_avanco() -> None:
    """A aula ja assistida continua assistida, e o percentual e refeito.

    A arvore de aprendizado e apagada e recriada, entao esta e a prova de que o
    progresso volta. Ele volta pela **aula**, nao pela publicacao, que e como o
    aluno pensa nele.

    O percentual precisa ser refeito porque o total de aulas mudou: quem tinha
    uma de duas (50%) passa a ter uma de tres (33%), e nao pode continuar em 50%.
    """
    assert ADMIN_URL
    seller_id, product_id, course_id = create_graph()
    offer_id = save_draft(seller_id, product_id, course_id)
    aula_assistida = _acrescenta_aula(seller_id, course_id, "Aula assistida")
    _acrescenta_aula(seller_id, course_id, "Aula pendente")
    publish_with_access(
        seller_id, product_id, course_id, offer_id, access_mode="free", course_version=4
    )

    comprador = buyer_id(9)
    _da_acesso(seller_id, product_id, comprador)
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            """
            insert into public.lesson_progress(
              seller_organization_id, enrollment_id, buyer_identity_user_id, entitlement_id,
              catalog_product_id, publication_id, course_id, lesson_id, progress_percent,
              completed_at)
            select matricula.seller_organization_id, matricula.id,
                   matricula.buyer_identity_user_id, matricula.entitlement_id,
                   matricula.catalog_product_id, matricula.publication_id,
                   matricula.course_id, %s, 100, clock_timestamp()
              from public.learning_enrollment matricula
             where matricula.seller_organization_id=%s
               and matricula.buyer_identity_user_id=%s
            """,
            (aula_assistida, seller_id, comprador),
        )
        connection.execute(
            "update public.course_learning_state set completed_lessons=1, progress_percent=50"
            " where seller_organization_id=%s and buyer_identity_user_id=%s",
            (seller_id, comprador),
        )
        connection.commit()
    _, total_antes, feitas_antes, percentual_antes = _estado_do_curso(seller_id, comprador)
    assert (total_antes, feitas_antes, percentual_antes) == (2, 1, 50)

    _acrescenta_aula(seller_id, course_id, "Aula nova")
    publish_with_access(
        seller_id,
        product_id,
        course_id,
        offer_id,
        access_mode="free",
        product_version=3,
        course_version=5,
    )

    _, publicacao_agora, _ = _acesso_do_comprador(seller_id, product_id, comprador)
    publicacao_do_estado, total, feitas, percentual = _estado_do_curso(seller_id, comprador)
    assert publicacao_do_estado == publicacao_agora, "o estado do curso ficou para tras"
    assert (total, feitas, percentual) == (3, 1, 33)

    with psycopg.connect(ADMIN_URL) as connection:
        progresso = connection.execute(
            "select feito.lesson_id, feito.progress_percent, feito.publication_id"
            "  from public.lesson_progress feito"
            " where feito.seller_organization_id=%s and feito.buyer_identity_user_id=%s",
            (seller_id, comprador),
        ).fetchall()
    assert progresso == [(aula_assistida, 100, publicacao_agora)]


def _cria_compra(
    seller_id: UUID, product_id: UUID, offer_id: UUID, comprador: UUID, publicacao: UUID
) -> UUID:
    """Amarra uma compra paga ao acesso, que e o caso que exercita a chave nova.

    O caminho gratuito nao passa pela `buyer_entitlement_demo_purchase_fkey`,
    entao sem isto o teste nao provaria nada sobre ela.
    """
    assert ADMIN_URL
    coupon_id, order_id, event_id, purchase_id = uuid4(), uuid4(), uuid4(), uuid4()
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute("set role appsell_owner")
        connection.execute(
            "insert into public.demo_coupon"
            " (seller_organization_id, id, code_sha256, discount_bps, status)"
            " values (%s,%s,%s,10000,'active')",
            (seller_id, coupon_id, digest(f"cupom:{coupon_id}")),
        )
        connection.execute(
            "insert into public.demo_checkout_offer"
            " (seller_organization_id, offer_id, catalog_product_id, publication_id,"
            "  entitlement_id, unit_amount_minor, currency, status)"
            " values (%s,%s,%s,%s,%s,9900,'BRL','active')"
            " on conflict do nothing",
            (seller_id, offer_id, product_id, publicacao, uuid4()),
        )
        connection.execute(
            "insert into public.demo_order"
            " (seller_organization_id, id, buyer_identity_user_id, offer_id, catalog_product_id,"
            "  publication_id, coupon_id, subtotal_amount_minor, discount_amount_minor,"
            "  total_amount_minor, currency, status)"
            " values (%s,%s,%s,%s,%s,%s,%s,9900,9900,0,'BRL','approved')",
            (seller_id, order_id, comprador, offer_id, product_id, publicacao, coupon_id),
        )
        connection.execute(
            "insert into public.demo_purchase_event"
            " (seller_organization_id, id, order_id, event_type, event_key)"
            " values (%s,%s,%s,'purchase.approved',%s)",
            (seller_id, event_id, order_id, f"demo-order:{order_id}:approved"),
        )
        connection.execute(
            "insert into public.demo_purchase"
            " (seller_organization_id, id, order_id, event_id, buyer_identity_user_id,"
            "  catalog_product_id, publication_id, status)"
            " values (%s,%s,%s,%s,%s,%s,%s,'approved')",
            (seller_id, purchase_id, order_id, event_id, comprador, product_id, publicacao),
        )
        connection.execute(
            "update public.buyer_entitlement set commerce_purchase_id=%s,"
            " source='demo_checkout'"
            " where seller_organization_id=%s and buyer_identity_user_id=%s"
            "   and catalog_product_id=%s",
            (purchase_id, seller_id, comprador, product_id),
        )
        connection.commit()
    return purchase_id


def test_quem_pagou_avanca_e_o_recibo_da_venda_fica_como_estava() -> None:
    """O caso do Rodrigo, medido em 04/09/2026, e a decisao que ele autorizou.

    A `buyer_entitlement_demo_purchase_fkey` carregava o `publication_id` e
    exigia, literalmente, que o acesso continuasse apontando para a publicacao
    que estava no recibo. Era a causa raiz do congelamento.

    Agora ela garante compra, comprador e produto, e para por ai. O recibo
    continua guardando o que foi vendido; ele so nao decide mais o que o aluno ve.
    """
    assert ADMIN_URL
    seller_id, product_id, course_id = create_graph()
    offer_id = save_draft(seller_id, product_id, course_id)
    _acrescenta_aula(seller_id, course_id, "Aula do lancamento")
    publish_with_access(
        seller_id, product_id, course_id, offer_id, access_mode="free", course_version=3
    )

    comprador = buyer_id(10)
    _da_acesso(seller_id, product_id, comprador)
    _, publicacao_da_compra, _ = _acesso_do_comprador(seller_id, product_id, comprador)
    compra = _cria_compra(seller_id, product_id, offer_id, comprador, publicacao_da_compra)

    _acrescenta_aula(seller_id, course_id, "Aula acrescentada depois")
    publish_with_access(
        seller_id,
        product_id,
        course_id,
        offer_id,
        access_mode="free",
        product_version=3,
        course_version=4,
    )

    _, publicacao_agora, _ = _acesso_do_comprador(seller_id, product_id, comprador)
    assert publicacao_agora != publicacao_da_compra, "quem pagou nao recebeu a atualizacao"

    with psycopg.connect(ADMIN_URL) as connection:
        recibo = connection.execute(
            "select publication_id from public.demo_purchase"
            " where seller_organization_id=%s and id=%s",
            (seller_id, compra),
        ).fetchone()
    assert recibo == (publicacao_da_compra,), "o recibo da venda foi reescrito"
