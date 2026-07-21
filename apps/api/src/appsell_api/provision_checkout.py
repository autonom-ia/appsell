"""Provision a checkout integration + webhook endpoint + product mapping.

Operational CLI (run at deploy time, red-band with backup) that makes a seller
able to receive signed checkout webhooks: inserts ``checkout_integration``,
``webhook_endpoint`` (endpoint key stored only as SHA-256) and one
``external_product_mapping`` row (external product id stored only as SHA-256).

Secrets never travel via argv: the plaintext endpoint key comes from
``APPSELL_PROVISION_ENDPOINT_KEY``; the webhook signing secret itself lives in
the API keyring env (``APPSELL_API_WEBHOOK_LOCAL_KEYRING_B64_JSON``) and only
its ``secret_reference`` name is recorded here. Idempotent: re-running with the
same inputs is a no-op; diverging inputs for an existing endpoint abort.

Usage:
  APPSELL_PROVISION_DATABASE_URL=postgresql://... \\
  APPSELL_PROVISION_ENDPOINT_KEY=<32-64 chars url-safe> \\
  python -m appsell_api.provision_checkout \\
    --seller-slug <slug> --secret-reference <ref> \\
    --external-product-id <financial product key> --product-slug <slug> \\
    --confirm provision-checkout-v1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from uuid import UUID

import psycopg

_CONFIRM = "provision-checkout-v1"
_ENDPOINT_KEY = re.compile(r"^[A-Za-z0-9_-]{32,64}$")
_SECRET_REFERENCE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seller-slug", required=True)
    parser.add_argument("--secret-reference", required=True)
    parser.add_argument("--external-product-id", required=True)
    parser.add_argument("--product-slug", required=True)
    parser.add_argument("--display-name", default="Checkout externo")
    parser.add_argument("--confirm", required=True)
    arguments = parser.parse_args()

    if arguments.confirm != _CONFIRM:
        _fail(f"confirmation key must be {_CONFIRM!r}")
    database_url = os.environ.get("APPSELL_PROVISION_DATABASE_URL", "")
    endpoint_key = os.environ.get("APPSELL_PROVISION_ENDPOINT_KEY", "")
    if not database_url:
        _fail("APPSELL_PROVISION_DATABASE_URL is required")
    if _ENDPOINT_KEY.fullmatch(endpoint_key) is None:
        _fail("APPSELL_PROVISION_ENDPOINT_KEY must be 32-64 url-safe characters")
    if _SECRET_REFERENCE.fullmatch(arguments.secret_reference) is None:
        _fail("secret reference is invalid")
    if _SLUG.fullmatch(arguments.seller_slug) is None:
        _fail("seller slug is invalid")
    if _SLUG.fullmatch(arguments.product_slug) is None:
        _fail("product slug is invalid")
    external_product = arguments.external_product_id
    if (
        external_product != external_product.strip()
        or not 1 <= len(external_product.encode("utf-8")) <= 255
    ):
        _fail("external product id is invalid")

    endpoint_key_sha256 = hashlib.sha256(endpoint_key.encode("utf-8")).hexdigest()
    product_sha256 = hashlib.sha256(external_product.encode("utf-8")).hexdigest()

    with psycopg.connect(database_url) as connection:
        seller_row = connection.execute(
            "select id from seller_organization where slug=%s and status='active'",
            (arguments.seller_slug,),
        ).fetchone()
        if seller_row is None:
            _fail("seller organization not found or inactive")
            return
        seller_id = UUID(str(seller_row[0]))
        product_row = connection.execute(
            "select id from catalog_product "
            "where seller_organization_id=%s and slug=%s",
            (seller_id, arguments.product_slug),
        ).fetchone()
        if product_row is None:
            _fail("catalog product not found for seller")
            return
        product_id = UUID(str(product_row[0]))

        existing = connection.execute(
            "select endpoint.id,endpoint.checkout_integration_id,"
            "endpoint.secret_reference,endpoint.status "
            "from webhook_endpoint endpoint "
            "where endpoint.seller_organization_id=%s "
            "and endpoint.endpoint_key_sha256=%s",
            (seller_id, endpoint_key_sha256),
        ).fetchone()
        if existing is not None:
            if existing[2] != arguments.secret_reference or existing[3] != "active":
                _fail("endpoint exists with diverging secret reference or status")
            integration_id = UUID(str(existing[1]))
        else:
            integration_row = connection.execute(
                "insert into checkout_integration("
                "seller_organization_id,adapter,display_name,status) "
                "values(%s,'generic_hmac_v1',%s,'active') returning id",
                (seller_id, arguments.display_name),
            ).fetchone()
            if integration_row is None:
                _fail("checkout integration insert returned no id")
                return
            integration_id = UUID(str(integration_row[0]))
            connection.execute(
                "insert into webhook_endpoint("
                "seller_organization_id,checkout_integration_id,endpoint_key_sha256,"
                "secret_reference,status) values(%s,%s,%s,%s,'active')",
                (seller_id, integration_id, endpoint_key_sha256, arguments.secret_reference),
            )
        connection.execute(
            "insert into external_product_mapping("
            "seller_organization_id,checkout_integration_id,external_product_id_sha256,"
            "external_product_id_octets,catalog_product_id) values(%s,%s,%s,%s,%s) "
            "on conflict (seller_organization_id,checkout_integration_id,"
            "external_product_id_sha256) do nothing",
            (
                seller_id,
                integration_id,
                product_sha256,
                len(external_product.encode("utf-8")),
                product_id,
            ),
        )
        mapping = connection.execute(
            "select catalog_product_id from external_product_mapping "
            "where seller_organization_id=%s and checkout_integration_id=%s "
            "and external_product_id_sha256=%s",
            (seller_id, integration_id, product_sha256),
        ).fetchone()
        if mapping is None or UUID(str(mapping[0])) != product_id:
            _fail("external product mapping points at a different product")
        connection.commit()

    print(
        json.dumps(
            {
                "status": "provisioned",
                "seller_organization_id": str(seller_id),
                "checkout_integration_id": str(integration_id),
                "catalog_product_id": str(product_id),
                "endpoint_key_sha256": endpoint_key_sha256,
                "secret_reference": arguments.secret_reference,
            }
        )
    )


def _fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
