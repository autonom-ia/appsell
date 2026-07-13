from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from appsell_api.reset_demo_tenant import load_manifest

CAPACITY_CONFIRMATION = "capacity-100-tenants-v1"
CAPACITY_DATABASE_MARKER = "appsell-capacity-target-v1"
CAPACITY_TENANTS = 100
CAPACITY_BUYERS_PER_TENANT = 10
CAPACITY_BUYERS = CAPACITY_TENANTS * CAPACITY_BUYERS_PER_TENANT
EXPECTED_MIGRATION = "0008_private_asset_storage"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed 100 deterministic tenants into an isolated Local/CI database."
    )
    parser.add_argument("--confirm", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def deterministic_id(kind: str, ordinal: int) -> UUID:
    return uuid5(NAMESPACE_URL, f"appsell-capacity-v1/{kind}/{ordinal:03d}")


def deterministic_buyer_id(kind: str, tenant_ordinal: int, buyer_ordinal: int) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"appsell-capacity-v2/{kind}/{tenant_ordinal:03d}/{buyer_ordinal:02d}",
    )


def identity_id(issuer: str, subject: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"{issuer}\0{subject}")


def require_environment(confirmation: str) -> tuple[str, str]:
    if confirmation != CAPACITY_CONFIRMATION:
        raise RuntimeError("capacity confirmation mismatch")
    environment = os.environ.get("APPSELL_API_ENVIRONMENT", "").lower()
    if environment not in {"local", "ci"}:
        raise RuntimeError("APPSELL_API_ENVIRONMENT must explicitly be local or ci")
    issuer = os.environ.get("APPSELL_API_IDENTITY_ISSUER", "").strip()
    if not issuer or any(ord(character) < 0x20 for character in issuer):
        raise RuntimeError("APPSELL_API_IDENTITY_ISSUER is required")
    database_url = os.environ.get("APPSELL_CAPACITY_DATABASE_URL", "")
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("APPSELL_CAPACITY_DATABASE_URL must be a PostgreSQL DSN")
    connection_info = conninfo_to_dict(database_url)
    loopback = {"127.0.0.1", "localhost", "::1"}
    if connection_info.get("host") not in loopback:
        raise RuntimeError("capacity database must be loopback-only")
    hostaddr = connection_info.get("hostaddr")
    if hostaddr and hostaddr not in loopback:
        raise RuntimeError("capacity database hostaddr must be loopback-only")
    if connection_info.get("service") or connection_info.get("servicefile"):
        raise RuntimeError("capacity database must not use libpq service indirection")
    inherited_routing = [
        name
        for name in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE")
        if os.environ.get(name, "").strip()
    ]
    if inherited_routing:
        raise RuntimeError(
            "capacity database must not inherit libpq routing: " + ",".join(inherited_routing)
        )
    return issuer, database_url


def validate_database(connection: psycopg.Connection[Any]) -> None:
    marker = connection.execute(
        "select shobj_description(oid,'pg_database') from pg_database "
        "where datname=current_database()"
    ).fetchone()
    if marker != (CAPACITY_DATABASE_MARKER,):
        raise RuntimeError("database is not marked as an AppSell capacity target")
    version = connection.execute("select version_num from alembic_version").fetchone()
    if version != (EXPECTED_MIGRATION,):
        raise RuntimeError(f"database must be migrated to {EXPECTED_MIGRATION}")
    existing = connection.execute(
        "select count(*) from identity_user where identity_sub like 'capacity-%'"
    ).fetchone()
    if existing != (0,):
        raise RuntimeError("capacity identities already exist; use a fresh disposable database")


def snapshot_for(tenant: dict[str, str]) -> dict[str, Any]:
    return {
        "schema_version": 3,
        "seller_organization_id": tenant["seller_id"],
        "product": {
            "id": tenant["product_id"],
            "slug": tenant["product_slug"],
            "title": tenant["product_title"],
            "description": "Produto sintético do certificado Local/CI de capacidade.",
        },
        "offer": {
            "id": tenant["offer_id"],
            "title": "Oferta de capacidade",
            "summary": "Sem autoridade de cobrança.",
            "billing_authority": "external_unconfigured",
        },
        "courses": [
            {
                "id": tenant["course_id"],
                "title": tenant["course_title"],
                "description": "Curso sintético isolado por tenant.",
                "position": 0,
                "modules": [
                    {
                        "id": tenant["module_id"],
                        "title": "Módulo único",
                        "status": "active",
                        "position": 0,
                        "lessons": [
                            {
                                "id": tenant["lesson_id"],
                                "title": "Aula de isolamento",
                                "kind": "text",
                                "content": "Conteúdo sintético para prova de isolamento.",
                                "status": "active",
                                "position": 0,
                                "assets": [
                                    {
                                        "id": tenant["asset_id"],
                                        "version": 1,
                                        "title": tenant["asset_title"],
                                        "file_name": tenant["asset_file_name"],
                                        "media_type": "application/pdf",
                                        "byte_size": int(tenant["asset_bytes"]),
                                        "sha256": tenant["asset_sha256"],
                                        "position": 0,
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    }


def buyer_manifest(issuer: str, tenant_ordinal: int, buyer_ordinal: int) -> dict[str, str | int]:
    subject = f"capacity-buyer-{tenant_ordinal:03d}-{buyer_ordinal:02d}"
    return {
        "ordinal": buyer_ordinal,
        "subject": subject,
        "identity_id": str(identity_id(issuer, subject)),
        "entitlement_id": str(deterministic_buyer_id("entitlement", tenant_ordinal, buyer_ordinal)),
    }


def tenant_manifest(issuer: str, ordinal: int) -> dict[str, Any]:
    demo_manifest, _manifest_sha = load_manifest()
    demo_lessons = demo_manifest["lessons"]
    demo_asset = demo_lessons[ordinal % len(demo_lessons)]
    owner_subject = f"capacity-owner-{ordinal:03d}"
    return {
        "ordinal": ordinal,
        "owner_subject": owner_subject,
        "owner_identity_id": str(identity_id(issuer, owner_subject)),
        "buyers": [
            buyer_manifest(issuer, ordinal, buyer_ordinal)
            for buyer_ordinal in range(CAPACITY_BUYERS_PER_TENANT)
        ],
        "seller_id": str(deterministic_id("seller", ordinal)),
        "seller_slug": f"capacity-seller-{ordinal:03d}",
        "product_id": str(deterministic_id("product", ordinal)),
        "product_slug": f"capacity-product-{ordinal:03d}",
        "product_title": f"Produto Capacidade {ordinal:03d}",
        "course_id": str(deterministic_id("course", ordinal)),
        "course_title": f"Curso Capacidade {ordinal:03d}",
        "module_id": str(deterministic_id("module", ordinal)),
        "lesson_id": str(deterministic_id("lesson", ordinal)),
        "offer_id": str(deterministic_id("offer", ordinal)),
        "publication_id": str(deterministic_id("publication", ordinal)),
        "asset_id": str(deterministic_id("asset", ordinal)),
        "asset_title": str(demo_asset["title"]),
        "asset_file_name": f"{demo_asset['slug']}.pdf",
        "asset_seed_key": str(demo_asset["slug"]),
        "asset_bytes": int(demo_asset["bytes"]),
        "asset_sha256": str(demo_asset["sha256"]),
    }


def seed_tenant(connection: psycopg.Connection[Any], tenant: dict[str, Any], issuer: str) -> None:
    owner_id = UUID(str(tenant["owner_identity_id"]))
    seller_id = UUID(str(tenant["seller_id"]))
    product_id = UUID(str(tenant["product_id"]))
    course_id = UUID(str(tenant["course_id"]))
    module_id = UUID(str(tenant["module_id"]))
    lesson_id = UUID(str(tenant["lesson_id"]))
    offer_id = UUID(str(tenant["offer_id"]))
    publication_id = UUID(str(tenant["publication_id"]))
    asset_id = UUID(str(tenant["asset_id"]))
    ordinal = int(tenant["ordinal"])

    connection.execute(
        """
        insert into identity_user(
          id,identity_issuer,identity_sub,email_normalized,email_verified
        ) values(%s,%s,%s,%s,true)
        """,
        (
            owner_id,
            issuer,
            tenant["owner_subject"],
            f"capacity.owner.{ordinal:03d}@autonomia.site",
        ),
    )
    for buyer in tenant["buyers"]:
        connection.execute(
            """
            insert into identity_user(
              id,identity_issuer,identity_sub,email_normalized,email_verified
            ) values(%s,%s,%s,%s,true)
            """,
            (
                UUID(str(buyer["identity_id"])),
                issuer,
                buyer["subject"],
                f"capacity.buyer.{ordinal:03d}.{int(buyer['ordinal']):02d}@autonomia.site",
            ),
        )
    connection.execute(
        "insert into seller_organization(id,slug,display_name,status) values(%s,%s,%s,'active')",
        (seller_id, tenant["seller_slug"], f"Seller Capacidade {ordinal:03d}"),
    )
    connection.execute(
        """
        insert into seller_membership(seller_organization_id,identity_user_id,role,status)
        values(%s,%s,'seller_owner','active')
        """,
        (seller_id, owner_id),
    )
    connection.execute(
        """
        insert into catalog_product(
          seller_organization_id,id,slug,title,description,status,version
        ) values(%s,%s,%s,%s,%s,'draft',1)
        """,
        (
            seller_id,
            product_id,
            tenant["product_slug"],
            tenant["product_title"],
            "Produto sintético do certificado Local/CI de capacidade.",
        ),
    )
    connection.execute(
        """
        insert into course(seller_organization_id,id,title,description,status,draft_version)
        values(%s,%s,%s,%s,'draft',1)
        """,
        (seller_id, course_id, tenant["course_title"], "Curso sintético isolado por tenant."),
    )
    connection.execute(
        """
        insert into product_course(seller_organization_id,catalog_product_id,course_id,position)
        values(%s,%s,%s,0)
        """,
        (seller_id, product_id, course_id),
    )
    connection.execute(
        """
        insert into offer(
          seller_organization_id,id,catalog_product_id,title,summary,status,version
        ) values(%s,%s,%s,'Oferta de capacidade','Sem autoridade de cobrança.','draft',1)
        """,
        (seller_id, offer_id, product_id),
    )
    connection.execute(
        """
        insert into course_module(seller_organization_id,id,course_id,title,status,position)
        values(%s,%s,%s,'Módulo único','active',0)
        """,
        (seller_id, module_id, course_id),
    )
    connection.execute(
        """
        insert into lesson(
          seller_organization_id,id,course_id,module_id,title,body_text,status,position
        ) values(%s,%s,%s,%s,'Aula de isolamento',
          'Conteúdo sintético para prova de isolamento.','active',0)
        """,
        (seller_id, lesson_id, course_id, module_id),
    )
    connection.execute(
        """
        insert into asset_record(
          seller_organization_id,id,catalog_product_id,title,status
        ) values(%s,%s,%s,%s,'active')
        """,
        (seller_id, asset_id, product_id, tenant["asset_title"]),
    )
    connection.execute(
        """
        insert into asset_blob(
          seller_organization_id,id,storage_driver,storage_bucket,object_key,
          file_name,media_type,byte_size,sha256,magic_type,scanner_result,verified_at
        ) values(%s,app_private.legacy_asset_blob_id(%s),'legacy_bundle','codex-demo',
          %s,%s,'application/pdf',%s,%s,'application/pdf','legacy_trusted',
          clock_timestamp())
        """,
        (
            seller_id,
            asset_id,
            tenant["asset_file_name"],
            tenant["asset_file_name"],
            tenant["asset_bytes"],
            tenant["asset_sha256"],
        ),
    )
    connection.execute(
        """
        insert into asset_version(
          seller_organization_id,asset_id,version,blob_id,actor_identity_user_id
        ) values(%s,%s,1,app_private.legacy_asset_blob_id(%s),%s)
        """,
        (seller_id, asset_id, asset_id, owner_id),
    )
    connection.execute(
        """
        insert into lesson_asset_draft(
          seller_organization_id,catalog_product_id,course_id,module_id,lesson_id,
          asset_id,asset_version,blob_id,position,status
        ) values(%s,%s,%s,%s,%s,%s,1,app_private.legacy_asset_blob_id(%s),0,'active')
        """,
        (seller_id, product_id, course_id, module_id, lesson_id, asset_id, asset_id),
    )
    connection.execute(
        """
        insert into learning_asset(
          seller_organization_id,id,catalog_product_id,course_id,module_id,lesson_id,
          seed_key,file_name,title,media_type,byte_size,sha256,position,status
        ) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,'application/pdf',%s,%s,0,'active')
        """,
        (
            seller_id,
            asset_id,
            product_id,
            course_id,
            module_id,
            lesson_id,
            tenant["asset_seed_key"],
            tenant["asset_file_name"],
            tenant["asset_title"],
            tenant["asset_bytes"],
            tenant["asset_sha256"],
        ),
    )
    snapshot = snapshot_for({key: str(value) for key, value in tenant.items()})
    connection.execute(
        """
        with payload as (select %s::jsonb as snapshot)
        insert into publication(
          seller_organization_id,id,catalog_product_id,version,schema_version,
          source_sha256,snapshot,actor_identity_user_id
        ) select %s,%s,%s,1,3,
          encode(digest(convert_to(snapshot::text,'UTF8'),'sha256'),'hex'),snapshot,%s
        from payload
        """,
        (Jsonb(snapshot), seller_id, publication_id, product_id, owner_id),
    )
    connection.execute(
        """
        update catalog_product set status='published',published_snapshot_id=%s
        where seller_organization_id=%s and id=%s
        """,
        (publication_id, seller_id, product_id),
    )
    connection.execute(
        "update course set status='published' where seller_organization_id=%s and id=%s",
        (seller_id, course_id),
    )
    for buyer in tenant["buyers"]:
        connection.execute(
            """
            insert into buyer_entitlement(
              seller_organization_id,id,buyer_identity_user_id,catalog_product_id,
              publication_id,reference_key,source,status,progress_percent
            ) values(%s,%s,%s,%s,%s,%s,'manual','active',0)
            """,
            (
                seller_id,
                UUID(str(buyer["entitlement_id"])),
                UUID(str(buyer["identity_id"])),
                product_id,
                publication_id,
                f"capacity-{ordinal:03d}-{int(buyer['ordinal']):02d}",
            ),
        )


def seed_capacity(connection: psycopg.Connection[Any], issuer: str) -> list[dict[str, Any]]:
    validate_database(connection)
    connection.execute(
        "select pg_advisory_xact_lock(hashtextextended(%s,0))", (CAPACITY_CONFIRMATION,)
    )
    tenants = [tenant_manifest(issuer, ordinal) for ordinal in range(CAPACITY_TENANTS)]
    for tenant in tenants:
        seed_tenant(connection, tenant, issuer)
    counts = connection.execute(
        """
        select
          count(distinct organization.id),
          count(distinct product.id),
          count(distinct entitlement.id),
          count(distinct membership.identity_user_id),
          count(distinct entitlement.buyer_identity_user_id),
          count(distinct course.id),
          count(distinct publication.id),
          count(distinct learning_asset.id)
        from seller_organization organization
        join seller_membership membership
          on membership.seller_organization_id=organization.id
        join catalog_product product on product.seller_organization_id=organization.id
        join product_course linked on linked.seller_organization_id=organization.id
          and linked.catalog_product_id=product.id
        join course on course.seller_organization_id=linked.seller_organization_id
          and course.id=linked.course_id
        join publication on publication.seller_organization_id=organization.id
          and publication.id=product.published_snapshot_id
        join buyer_entitlement entitlement
          on entitlement.seller_organization_id=organization.id
        join learning_asset on learning_asset.seller_organization_id=organization.id
        where organization.slug like 'capacity-seller-%'
        """
    ).fetchone()
    expected_counts = (
        CAPACITY_TENANTS,
        CAPACITY_TENANTS,
        CAPACITY_BUYERS,
        CAPACITY_TENANTS,
        CAPACITY_BUYERS,
        CAPACITY_TENANTS,
        CAPACITY_TENANTS,
        CAPACITY_TENANTS,
    )
    if counts != expected_counts:
        raise RuntimeError(f"capacity fixture count mismatch: {counts}")
    return tenants


def main() -> None:
    args = parse_args()
    issuer, database_url = require_environment(args.confirm)
    with psycopg.connect(database_url) as connection:
        tenants = seed_capacity(connection, issuer)
    report = {
        "schema_version": 2,
        "mode": "local_ci",
        "tenant_count": CAPACITY_TENANTS,
        "admin_count": CAPACITY_TENANTS,
        "buyers_per_tenant": CAPACITY_BUYERS_PER_TENANT,
        "buyer_count": CAPACITY_BUYERS,
        "distinct_subjects": CAPACITY_TENANTS + CAPACITY_BUYERS,
        "tenants": tenants,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
    print(json.dumps({key: report[key] for key in report if key != "tenants"}, sort_keys=True))


if __name__ == "__main__":
    main()
