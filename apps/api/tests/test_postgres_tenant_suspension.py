from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.environ.get("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY_HEX = os.environ.get("APPSELL_TEST_CONTEXT_KEY_HEX")

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY_HEX)),
    reason="isolated PostgreSQL environment is required",
)

ISSUER = "https://identity.test.autonomia.site"
SUPERADMIN_ID = UUID("91000000-0000-4000-8000-000000000119")
SUPERADMIN_SUB = "tenant-status-superadmin"
OWNER_ID = UUID("91000000-0000-4000-8000-000000000219")
OWNER_SUB = "tenant-status-owner"
TENANT_ID = UUID("92000000-0000-4000-8000-000000000119")
CLOSED_TENANT_ID = UUID("92000000-0000-4000-8000-000000000219")
PRODUCT_ID = UUID("93000000-0000-4000-8000-000000000119")
COURSE_ID = UUID("94000000-0000-4000-8000-000000000119")


def apply_context(
    connection: psycopg.Connection[Any], actor: UUID, seller: UUID | None, capability: str
) -> None:
    issued = int(time.time())
    expires = issued + 60
    payload = f"{actor}:{seller or ''}:{capability}:{issued}:{expires}"
    signature = hmac.new(
        bytes.fromhex(CONTEXT_KEY_HEX or ""), payload.encode(), hashlib.sha256
    ).hexdigest()
    connection.execute(
        "select app_private.apply_request_context(%s,%s,%s,%s,%s,%s)",
        (actor, seller, capability, issued, expires, signature),
    )


def set_status(target_status: str, reason: str) -> dict[str, Any]:
    assert RUNTIME_URL
    capability = (
        "platform.tenant.suspend" if target_status == "suspended" else "platform.tenant.reactivate"
    )
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, SUPERADMIN_ID, None, capability)
        row = connection.execute(
            "select app_private.set_tenant_status(%s,%s,%s,%s,%s,%s)",
            (SUPERADMIN_ID, SUPERADMIN_SUB, TENANT_ID, target_status, reason, uuid4()),
        ).fetchone()
        connection.commit()
    assert row is not None and isinstance(row[0], dict)
    return row[0]


@pytest.fixture(scope="module", autouse=True)
def seed_platform_actor_and_composite_tenant_graph() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "insert into identity_user("
            "id,identity_issuer,identity_sub,email_normalized,email_verified) "
            "values(%s,%s,%s,'tenant.status.superadmin@example.test',true),"
            "(%s,%s,%s,'tenant.status.owner@example.test',true)",
            (SUPERADMIN_ID, ISSUER, SUPERADMIN_SUB, OWNER_ID, ISSUER, OWNER_SUB),
        )
        connection.execute(
            "insert into platform_grant(identity_user_id,role,status) "
            "values(%s,'platform_superadmin','active')",
            (SUPERADMIN_ID,),
        )
        connection.execute(
            "insert into seller_organization(id,slug,display_name,status) "
            "values(%s,'tenant-status-0019','Tenant Status 0019','active'),"
            "(%s,'tenant-closed-0019','Tenant Closed 0019','closed')",
            (TENANT_ID, CLOSED_TENANT_ID),
        )
        connection.execute(
            "insert into seller_membership(seller_organization_id,identity_user_id,role,status) "
            "values(%s,%s,'seller_owner','active')",
            (TENANT_ID, OWNER_ID),
        )
        connection.execute(
            "insert into catalog_product(seller_organization_id,id,slug,title,description,status) "
            "values(%s,%s,'tenant-status-product','Tenant Status Product',"
            "'Graph preserved','draft')",
            (TENANT_ID, PRODUCT_ID),
        )
        connection.execute(
            "insert into course(seller_organization_id,id,title,description,status,draft_version) "
            "values(%s,%s,'Tenant Status Course','Graph preserved','draft',1)",
            (TENANT_ID, COURSE_ID),
        )
        connection.execute(
            "insert into product_course("
            "seller_organization_id,catalog_product_id,course_id,position) "
            "values(%s,%s,%s,0)",
            (TENANT_ID, PRODUCT_ID, COURSE_ID),
        )
        connection.commit()


def test_suspend_is_audited_preserves_composite_graph_and_blocks_tenant_context() -> None:
    assert ADMIN_URL and RUNTIME_URL
    result = set_status("suspended", "Risco operacional confirmado")
    assert result == {"id": str(TENANT_ID), "status": "suspended", "applied": True}

    with psycopg.connect(ADMIN_URL) as connection:
        state = connection.execute(
            """
            select
              (select status from seller_organization where id=%s),
              (select count(*) from seller_membership where seller_organization_id=%s),
              (select count(*) from product_course where seller_organization_id=%s
                and catalog_product_id=%s and course_id=%s),
              (select relrowsecurity and relforcerowsecurity from pg_class
                where oid='public.seller_organization'::regclass),
              (select count(*) from audit_event where seller_organization_id=%s
                and actor_identity_user_id=%s and action='platform.tenant.suspended'
                and target_type='seller_organization' and target_id=%s
                and metadata @> '{"previous_status":"active","new_status":"suspended",
                  "applied":true,"reason":"Risco operacional confirmado"}'::jsonb)
            """,
            (
                TENANT_ID,
                TENANT_ID,
                TENANT_ID,
                PRODUCT_ID,
                COURSE_ID,
                TENANT_ID,
                SUPERADMIN_ID,
                TENANT_ID,
            ),
        ).fetchone()
    assert state == ("suspended", 1, 1, True, 1)

    with psycopg.connect(RUNTIME_URL) as connection:
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            apply_context(connection, OWNER_ID, TENANT_ID, "seller.read")
        connection.rollback()

        apply_context(connection, OWNER_ID, None, "seller.list")
        assert (
            connection.execute(
                "select organization.id from seller_organization organization "
                "join seller_membership membership "
                "on membership.seller_organization_id=organization.id "
                "where membership.identity_user_id=%s",
                (OWNER_ID,),
            ).fetchall()
            == []
        )
        connection.rollback()
        assert connection.execute(
            "select app_private.read_public_storefront('tenant-status-0019')"
        ).fetchone() == (None,)


def test_only_active_superadmin_can_reactivate_and_every_call_is_audited() -> None:
    assert ADMIN_URL and RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, OWNER_ID, None, "platform.tenant.reactivate")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select app_private.set_tenant_status(%s,%s,%s,'active',%s,%s)",
                (OWNER_ID, OWNER_SUB, TENANT_ID, "Tentativa sem grant global", uuid4()),
            )

    result = set_status("active", "Risco operacional resolvido")
    assert result == {"id": str(TENANT_ID), "status": "active", "applied": True}
    repeated = set_status("active", "Confirmacao operacional repetida")
    assert repeated == {"id": str(TENANT_ID), "status": "active", "applied": False}

    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, OWNER_ID, TENANT_ID, "seller.read")
        assert connection.execute(
            "select status from seller_organization where id=%s", (TENANT_ID,)
        ).fetchone() == ("active",)

    with psycopg.connect(ADMIN_URL) as connection:
        audit = connection.execute(
            "select metadata->>'previous_status',metadata->>'new_status',"
            "(metadata->>'applied')::boolean from audit_event "
            "where seller_organization_id=%s and action='platform.tenant.reactivated' "
            "order by created_at,id",
            (TENANT_ID,),
        ).fetchall()
    assert audit == [("suspended", "active", True), ("active", "active", False)]


def test_runtime_has_function_only_and_missing_or_closed_tenants_fail_closed() -> None:
    assert ADMIN_URL and RUNTIME_URL
    with psycopg.connect(ADMIN_URL) as connection:
        privileges = connection.execute(
            "select has_function_privilege('appsell_runtime',"
            "'app_private.set_tenant_status(uuid,text,uuid,text,text,uuid)','EXECUTE'),"
            "has_table_privilege('appsell_runtime','seller_organization','UPDATE'),"
            "not exists(select 1 from pg_proc function "
            "cross join lateral aclexplode(coalesce(function.proacl,"
            "acldefault('f',function.proowner))) privilege "
            "where function.oid='app_private.set_tenant_status(uuid,text,uuid,text,text,uuid)'"
            "::regprocedure and privilege.grantee=0 "
            "and privilege.privilege_type='EXECUTE')"
        ).fetchone()
    assert privileges == (True, False, True)

    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, SUPERADMIN_ID, None, "platform.tenant.suspend")
        assert connection.execute(
            "select app_private.set_tenant_status(%s,%s,%s,'suspended',%s,%s)",
            (
                SUPERADMIN_ID,
                SUPERADMIN_SUB,
                uuid4(),
                "Tenant ausente nao deve vazar dados",
                uuid4(),
            ),
        ).fetchone() == (None,)
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select app_private.set_tenant_status(%s,%s,%s,'suspended',%s,%s)",
                (
                    SUPERADMIN_ID,
                    SUPERADMIN_SUB,
                    CLOSED_TENANT_ID,
                    "Tenant encerrado permanece imutavel",
                    uuid4(),
                ),
            )
