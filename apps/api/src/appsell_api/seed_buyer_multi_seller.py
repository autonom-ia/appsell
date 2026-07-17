from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from .reset_demo_tenant import (
    DEMO_DATABASE_MARKER,
    EXPECTED_MIGRATION,
    identity_id,
    load_manifest,
    snapshot_for,
)

CONFIRMATION = "buyer-multi-seller-v1"
NAMESPACE = UUID("01a6463f-d696-4c34-b939-237fd53a7edf")


def fixture_id(name: str) -> UUID:
    return uuid5(NAMESPACE, name)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed the disposable Local/CI buyer A+B certification fixture."
    )
    parser.add_argument("--action", choices=("seed", "revoke-a"), default="seed")
    parser.add_argument("--confirm", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def require_environment(confirmation: str) -> tuple[str, str]:
    if confirmation != CONFIRMATION:
        raise RuntimeError("multi-seller confirmation mismatch")
    if os.environ.get("APPSELL_API_ENVIRONMENT", "").lower() not in {"local", "ci"}:
        raise RuntimeError("APPSELL_API_ENVIRONMENT must explicitly be local or ci")
    if os.environ.get("APPSELL_MULTI_SELLER_ENABLED", "").lower() != "true":
        raise RuntimeError("APPSELL_MULTI_SELLER_ENABLED must be true")
    issuer = os.environ.get("APPSELL_API_IDENTITY_ISSUER", "").strip()
    if not issuer or any(ord(character) < 0x20 for character in issuer):
        raise RuntimeError("APPSELL_API_IDENTITY_ISSUER is required")
    database_url = os.environ.get("APPSELL_MULTI_SELLER_DATABASE_URL", "")
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("APPSELL_MULTI_SELLER_DATABASE_URL must be a PostgreSQL DSN")
    connection_info = conninfo_to_dict(database_url)
    loopback = {"127.0.0.1", "localhost", "::1"}
    if connection_info.get("host") not in loopback:
        raise RuntimeError("multi-seller database must be loopback-only")
    hostaddr = connection_info.get("hostaddr")
    if hostaddr and hostaddr not in loopback:
        raise RuntimeError("multi-seller database hostaddr must be loopback-only")
    if connection_info.get("service") or connection_info.get("servicefile"):
        raise RuntimeError("multi-seller database must not use libpq service indirection")
    inherited_routing = [
        name
        for name in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE")
        if os.environ.get(name, "").strip()
    ]
    if inherited_routing:
        raise RuntimeError(
            "multi-seller database must not inherit libpq routing: " + ",".join(inherited_routing)
        )
    return issuer, database_url


def validate_database(connection: psycopg.Connection[Any]) -> None:
    marker = connection.execute(
        "select shobj_description(oid,'pg_database') from pg_database "
        "where datname=current_database()"
    ).fetchone()
    if marker != (DEMO_DATABASE_MARKER,):
        raise RuntimeError("database is not marked as an AppSell demo reset target")
    version = connection.execute("select version_num from alembic_version").fetchone()
    if version != (EXPECTED_MIGRATION,):
        raise RuntimeError(f"database must be migrated to {EXPECTED_MIGRATION}")


def insert_publication(
    connection: psycopg.Connection[Any],
    *,
    seller_id: UUID,
    product_id: UUID,
    publication_id: UUID,
    actor_id: UUID,
    snapshot: dict[str, Any],
) -> None:
    connection.execute(
        """
        with payload as (select %s::jsonb snapshot)
        insert into publication(
          seller_organization_id,id,catalog_product_id,version,schema_version,
          source_sha256,snapshot,actor_identity_user_id
        ) select %s,%s,%s,1,%s,
          encode(digest(convert_to(snapshot::text,'UTF8'),'sha256'),'hex'),snapshot,%s
        from payload
        """,
        (
            Jsonb(snapshot),
            seller_id,
            publication_id,
            product_id,
            int(snapshot["schema_version"]),
            actor_id,
        ),
    )
    connection.execute(
        "update catalog_product set status='published',published_snapshot_id=%s "
        "where seller_organization_id=%s and id=%s",
        (publication_id, seller_id, product_id),
    )


def seed(connection: psycopg.Connection[Any], issuer: str) -> dict[str, Any]:
    validate_database(connection)
    connection.execute("select pg_advisory_xact_lock(hashtextextended(%s,0))", (CONFIRMATION,))
    manifest, _manifest_sha = load_manifest()
    seller_a = UUID(manifest["seller"]["id"])
    product_a = UUID(manifest["product"]["id"])
    entitlement_a = UUID(manifest["entitlement"]["id"])
    buyer = identity_id(issuer, manifest["personas"]["buyer"]["subject"])
    admin = identity_id(issuer, manifest["personas"]["admin"]["subject"])
    base = connection.execute(
        "select count(*) from buyer_entitlement where id=%s and buyer_identity_user_id=%s "
        "and seller_organization_id=%s and status='active'",
        (entitlement_a, buyer, seller_a),
    ).fetchone()
    if base != (1,):
        raise RuntimeError("demo buyer entitlement A is not active")
    if connection.execute(
        "select count(*) from seller_organization where id=%s", (fixture_id("seller-b"),)
    ).fetchone() != (0,):
        raise RuntimeError("multi-seller fixture already exists")

    course_a = UUID(manifest["course"]["id"])
    lesson_a = UUID(manifest["lessons"][0]["lesson_id"])
    asset_a = UUID(manifest["lessons"][0]["asset_id"])
    extra_a = {
        "product": fixture_id("product-a-other"),
        "offer": fixture_id("offer-a-other"),
        "publication": fixture_id("publication-a-other"),
        "draft": fixture_id("product-a-draft"),
    }
    connection.execute(
        "insert into catalog_product(seller_organization_id,id,slug,title,description,status) "
        "values(%s,%s,'seller-a-outro','Outro produto A','Publicado no seller A','draft')",
        (seller_a, extra_a["product"]),
    )
    connection.execute(
        "insert into catalog_product(seller_organization_id,id,slug,title,description,status) "
        "values(%s,%s,'seller-a-rascunho','Rascunho secreto A','Nunca público','draft')",
        (seller_a, extra_a["draft"]),
    )
    connection.execute(
        "insert into product_course(seller_organization_id,catalog_product_id,course_id,position) "
        "values(%s,%s,%s,0)",
        (seller_a, extra_a["product"], course_a),
    )
    connection.execute(
        "insert into offer(seller_organization_id,id,catalog_product_id,title,summary,status) "
        "values(%s,%s,%s,'Oferta A adicional','Somente vitrine','draft')",
        (seller_a, extra_a["offer"], extra_a["product"]),
    )
    snapshot_a = snapshot_for(manifest)
    snapshot_a["schema_version"] = 2
    for course_item in snapshot_a["courses"]:
        for module_item in course_item["modules"]:
            for lesson_item in module_item["lessons"]:
                lesson_item.pop("assets", None)
    snapshot_a["product"] = {
        "id": str(extra_a["product"]),
        "slug": "seller-a-outro",
        "title": "Outro produto A",
        "description": "Publicado no seller A",
    }
    snapshot_a["offer"] = {
        "id": str(extra_a["offer"]),
        "title": "Oferta A adicional",
        "summary": "Somente vitrine",
        "billing_authority": "external_unconfigured",
    }
    insert_publication(
        connection,
        seller_id=seller_a,
        product_id=extra_a["product"],
        publication_id=extra_a["publication"],
        actor_id=admin,
        snapshot=snapshot_a,
    )

    seller_b = fixture_id("seller-b")
    product_b = fixture_id("product-b")
    course_b = fixture_id("course-b")
    module_b = fixture_id("module-b")
    lesson_b = fixture_id("lesson-b")
    asset_b = fixture_id("asset-b")
    offer_b = fixture_id("offer-b")
    publication_b = fixture_id("publication-b")
    entitlement_b = fixture_id("entitlement-b")
    video_source = manifest["video"]
    connection.execute(
        "insert into seller_organization(id,slug,display_name,status) "
        "values(%s,'buyer-multi-seller-b','Seller B','active')",
        (seller_b,),
    )
    connection.execute(
        "insert into seller_membership(seller_organization_id,identity_user_id,role,status) "
        "values(%s,%s,'seller_owner','active')",
        (seller_b, admin),
    )
    connection.execute(
        "insert into catalog_product(seller_organization_id,id,slug,title,description,status) "
        "values(%s,%s,'produto-b','Produto comprado B','Publicado no seller B','draft')",
        (seller_b, product_b),
    )
    connection.execute(
        "insert into course(seller_organization_id,id,title,description,status) "
        "values(%s,%s,'Curso B','Curso independente B','draft')",
        (seller_b, course_b),
    )
    connection.execute(
        "insert into product_course(seller_organization_id,catalog_product_id,course_id,position) "
        "values(%s,%s,%s,0)",
        (seller_b, product_b, course_b),
    )
    connection.execute(
        "insert into offer(seller_organization_id,id,catalog_product_id,title,summary,status) "
        "values(%s,%s,%s,'Oferta B','Sem billing','draft')",
        (seller_b, offer_b, product_b),
    )
    connection.execute(
        "insert into course_module(seller_organization_id,id,course_id,title,status,position) "
        "values(%s,%s,%s,'Módulo B','active',0)",
        (seller_b, module_b, course_b),
    )
    connection.execute(
        "insert into lesson(seller_organization_id,id,course_id,module_id,title,body_text,"
        "status,position) values(%s,%s,%s,%s,'Aula B','Conteúdo B','active',0)",
        (seller_b, lesson_b, course_b, module_b),
    )
    connection.execute(
        "insert into asset_record(seller_organization_id,id,catalog_product_id,title,status) "
        "values(%s,%s,%s,'Vídeo B','active')",
        (seller_b, asset_b, product_b),
    )
    connection.execute(
        """
        insert into asset_blob(
          seller_organization_id,id,storage_driver,storage_bucket,object_key,file_name,
          media_type,byte_size,sha256,magic_type,scanner_result,verified_at
        ) values(%s,app_private.legacy_asset_blob_id(%s),'legacy_bundle','codex-demo',
          %s,%s,'video/mp4',%s,%s,'video/mp4','legacy_trusted',clock_timestamp())
        """,
        (
            seller_b,
            asset_b,
            video_source["file_name"],
            video_source["file_name"],
            video_source["bytes"],
            video_source["sha256"],
        ),
    )
    connection.execute(
        "insert into asset_version(seller_organization_id,asset_id,version,blob_id,"
        "actor_identity_user_id) values(%s,%s,1,app_private.legacy_asset_blob_id(%s),%s)",
        (seller_b, asset_b, asset_b, admin),
    )
    connection.execute(
        """
        insert into lesson_asset_draft(
          seller_organization_id,catalog_product_id,course_id,module_id,lesson_id,
          asset_id,asset_version,blob_id,position,status
        ) values(%s,%s,%s,%s,%s,%s,1,app_private.legacy_asset_blob_id(%s),0,'active')
        """,
        (seller_b, product_b, course_b, module_b, lesson_b, asset_b, asset_b),
    )
    connection.execute(
        """
        insert into learning_asset(
          seller_organization_id,id,catalog_product_id,course_id,module_id,lesson_id,
          seed_key,file_name,title,media_type,byte_size,sha256,position,status
        ) values(%s,%s,%s,%s,%s,%s,%s,%s,'Vídeo B','video/mp4',%s,%s,0,'active')
        """,
        (
            seller_b,
            asset_b,
            product_b,
            course_b,
            module_b,
            lesson_b,
            video_source["slug"],
            video_source["file_name"],
            video_source["bytes"],
            video_source["sha256"],
        ),
    )
    snapshot_b = {
        "schema_version": 3,
        "seller_organization_id": str(seller_b),
        "product": {
            "id": str(product_b),
            "slug": "produto-b",
            "title": "Produto comprado B",
            "description": "Publicado no seller B",
        },
        "offer": {
            "id": str(offer_b),
            "title": "Oferta B",
            "summary": "Sem billing",
            "billing_authority": "external_unconfigured",
        },
        "courses": [
            {
                "id": str(course_b),
                "title": "Curso B",
                "description": "Curso independente B",
                "position": 0,
                "modules": [
                    {
                        "id": str(module_b),
                        "title": "Módulo B",
                        "status": "active",
                        "position": 0,
                        "lessons": [
                            {
                                "id": str(lesson_b),
                                "title": "Aula B",
                                "kind": "text",
                                "content": "Conteúdo B",
                                "status": "active",
                                "position": 0,
                                "assets": [
                                    {
                                        "id": str(asset_b),
                                        "version": 1,
                                        "title": "Vídeo B",
                                        "file_name": video_source["file_name"],
                                        "media_type": "video/mp4",
                                        "byte_size": video_source["bytes"],
                                        "sha256": video_source["sha256"],
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
    insert_publication(
        connection,
        seller_id=seller_b,
        product_id=product_b,
        publication_id=publication_b,
        actor_id=admin,
        snapshot=snapshot_b,
    )
    connection.execute(
        "update course set status='published' where seller_organization_id=%s and id=%s",
        (seller_b, course_b),
    )
    connection.execute(
        """
        insert into buyer_entitlement(
          seller_organization_id,id,buyer_identity_user_id,catalog_product_id,
          publication_id,reference_key,source,status,progress_percent
        ) values(%s,%s,%s,%s,%s,'buyer-global-seller-b','manual','active',0)
        """,
        (seller_b, entitlement_b, buyer, product_b, publication_b),
    )
    return {
        "schema_version": 1,
        "mode": "local_ci",
        "buyer_subject": manifest["personas"]["buyer"]["subject"],
        "buyer_identity_id": str(buyer),
        "seller_a": str(seller_a),
        "seller_b": str(seller_b),
        "product_a": str(product_a),
        "product_b": str(product_b),
        "entitlement_a": str(entitlement_a),
        "entitlement_b": str(entitlement_b),
        "lesson_a": str(lesson_a),
        "lesson_b": str(lesson_b),
        "asset_a": str(asset_a),
        "asset_b": str(asset_b),
        "other_product_a": str(extra_a["product"]),
        "draft_product_a": str(extra_a["draft"]),
    }


def revoke_a(connection: psycopg.Connection[Any], issuer: str) -> None:
    validate_database(connection)
    manifest, _manifest_sha = load_manifest()
    buyer = identity_id(issuer, manifest["personas"]["buyer"]["subject"])
    entitlement = UUID(manifest["entitlement"]["id"])
    cursor = connection.execute(
        "update buyer_entitlement set status='revoked',updated_at=clock_timestamp() "
        "where id=%s and buyer_identity_user_id=%s and status='active'",
        (entitlement, buyer),
    )
    if cursor.rowcount != 1:
        raise RuntimeError("buyer entitlement A was not active")


def main() -> None:
    args = parse_args()
    issuer, database_url = require_environment(args.confirm)
    with psycopg.connect(database_url) as connection:
        if args.action == "seed":
            fixture = seed(connection, issuer)
        else:
            revoke_a(connection, issuer)
            fixture = None
    if fixture is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(fixture, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "seeded" if args.action == "seed" else "revoked",
                "schema_version": 1,
                "mode": "local_ci",
            }
        )
    )


if __name__ == "__main__":
    main()
