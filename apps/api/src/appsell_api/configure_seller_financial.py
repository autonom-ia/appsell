"""Configure a seller's Financial operator and catalog item binding.

Usage:
  APPSELL_PROVISION_DATABASE_URL=postgresql://... \\
  python -m appsell_api.configure_seller_financial \\
    --seller-slug hub2you \\
    --financial-operator-id <uuid> \\
    --financial-product-catalog-item-id <uuid> \\
    --confirm configure-seller-financial-v1
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import NoReturn
from uuid import UUID

import psycopg

_CONFIRM = "configure-seller-financial-v1"
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seller-slug", required=True)
    parser.add_argument("--financial-operator-id", required=True, type=UUID)
    parser.add_argument("--financial-product-catalog-item-id", required=True, type=UUID)
    parser.add_argument("--confirm", required=True)
    arguments = parser.parse_args()

    if arguments.confirm != _CONFIRM:
        _fail(f"confirmation key must be {_CONFIRM!r}")
    if _SLUG.fullmatch(arguments.seller_slug) is None:
        _fail("seller slug is invalid")
    database_url = os.environ.get("APPSELL_PROVISION_DATABASE_URL", "")
    if not database_url:
        _fail("APPSELL_PROVISION_DATABASE_URL is required")

    with psycopg.connect(database_url) as connection:
        row = connection.execute(
            "update seller_organization set financial_operator_id=%s, "
            "financial_product_catalog_item_id=%s, updated_at=clock_timestamp() "
            "where slug=%s and status='active' and (financial_operator_id, "
            "financial_product_catalog_item_id) is distinct from (%s,%s) returning id",
            (
                arguments.financial_operator_id,
                arguments.financial_product_catalog_item_id,
                arguments.seller_slug,
                arguments.financial_operator_id,
                arguments.financial_product_catalog_item_id,
            ),
        ).fetchone()
        changed = row is not None
        if row is None:
            row = connection.execute(
                "select id from seller_organization where slug=%s and status='active' "
                "and financial_operator_id=%s and financial_product_catalog_item_id=%s",
                (
                    arguments.seller_slug,
                    arguments.financial_operator_id,
                    arguments.financial_product_catalog_item_id,
                ),
            ).fetchone()
        if row is None:
            _fail("seller organization not found, inactive, or could not be configured")
        connection.commit()

    print(
        json.dumps(
            {
                "status": "configured",
                "changed": changed,
                "seller_organization_id": str(row[0]),
                "financial_operator_id": str(arguments.financial_operator_id),
                "financial_product_catalog_item_id": str(
                    arguments.financial_product_catalog_item_id
                ),
            }
        )
    )


def _fail(message: str) -> NoReturn:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
