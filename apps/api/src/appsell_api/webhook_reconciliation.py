"""Report purchases that should have become access and did not (#147).

Read-only. Calls ``app_private.checkout_reconciliation_report`` (migration 0062)
inside a READ ONLY transaction and prints one JSON report on stdout. Every
divergence also goes to stderr as one ``appsell.reconciliation.divergence``
line, so a log search finds it without parsing the report.

Exit codes: 0 no divergence, 1 divergences found, 2 the report could not run.
Nothing is repaired here: each category says what the operation must check.

Run it as ``appsell_reconciler``, never as the migrator: that role has only
``connect``, ``usage`` on ``app_private`` and ``execute`` on this one function,
and starts every transaction read-only. The migrator inherits ``appsell_owner``,
which can run DDL.

Usage:
  APPSELL_RECONCILIATION_DATABASE_URL=postgresql://appsell_reconciler@... \\
  python -m appsell_api.webhook_reconciliation \\
    --since 2026-10-20T00:00:00Z --before 2026-10-23T00:00:00Z [--stale-minutes 60]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn

import psycopg

_DEFAULT_STALE_MINUTES = 60
_COLUMNS = (
    "category",
    "seller_organization_id",
    "seller_slug",
    "reference_type",
    "reference_id",
    "event_type",
    "external_purchase_id_sha256",
    "catalog_product_id",
    "status",
    "error_code",
    "observed_at",
)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True, type=_utc_instant)
    parser.add_argument("--before", required=True, type=_utc_instant)
    parser.add_argument("--stale-minutes", type=int, default=_DEFAULT_STALE_MINUTES)
    arguments = parser.parse_args(argv)
    database_url = os.environ.get("APPSELL_RECONCILIATION_DATABASE_URL", "")
    if not database_url:
        _fail("APPSELL_RECONCILIATION_DATABASE_URL is required")
    try:
        rows = read_divergences(
            database_url,
            since=arguments.since,
            before=arguments.before,
            stale_after=timedelta(minutes=arguments.stale_minutes),
        )
    except psycopg.Error as error:
        # The SQLSTATE says what failed without echoing the connection string.
        _fail(f"reconciliation report failed (sqlstate {error.sqlstate or 'unknown'})")
    report = build_report(rows, since=arguments.since, before=arguments.before)
    for divergence in report["divergences"]:
        print(
            json.dumps({"marker": "appsell.reconciliation.divergence", **divergence}),
            file=sys.stderr,
        )
    print(json.dumps(report, sort_keys=True))
    raise SystemExit(1 if report["divergences"] else 0)


def read_divergences(
    database_url: str, *, since: datetime, before: datetime, stale_after: timedelta
) -> list[dict[str, Any]]:
    with psycopg.connect(database_url) as connection:
        connection.read_only = True
        rows = connection.execute(
            "select category, seller_organization_id, seller_slug, reference_type, "
            "reference_id, event_type, external_purchase_id_sha256, catalog_product_id, "
            "status, error_code, observed_at "
            "from app_private.checkout_reconciliation_report(%s, %s, %s) "
            "order by category, seller_slug, observed_at, reference_id",
            (since, before, stale_after),
        ).fetchall()
    return [dict(zip(_COLUMNS, row, strict=True)) for row in rows]


def build_report(
    rows: Sequence[dict[str, Any]], *, since: datetime, before: datetime
) -> dict[str, Any]:
    divergences = [{key: _json_value(value) for key, value in row.items()} for row in rows]
    return {
        "schema_version": 1,
        "window": {"since": since.isoformat(), "before": before.isoformat()},
        "counts": dict(Counter(row["category"] for row in divergences)),
        "by_seller": dict(Counter(row["seller_slug"] for row in divergences)),
        "divergences": divergences,
    }


def _json_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, str | int):
        return value
    return str(value)


def _utc_instant(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected an ISO 8601 instant") from error
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("the instant must carry a timezone")
    return parsed.astimezone(UTC)


def _fail(message: str) -> NoReturn:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
