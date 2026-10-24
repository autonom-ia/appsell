from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import psycopg
import pytest

from appsell_api import webhook_reconciliation

SINCE = "2026-10-20T00:00:00Z"
BEFORE = "2026-10-23T00:00:00Z"
DATABASE_URL = "postgresql://migrator:secret-value@db.internal/appsell"

DIVERGENCE: dict[str, Any] = {
    "category": "fulfillment_dead_letter",
    "seller_organization_id": UUID("ee000000-0000-4000-8000-0000000000a1"),
    "seller_slug": "loja-a",
    "reference_type": "fulfillment_job",
    "reference_id": UUID("ee000000-0000-4000-8000-0000000000a2"),
    "event_type": "purchase.approved",
    "external_purchase_id_sha256": "a" * 64,
    "catalog_product_id": None,
    "status": "dead_letter",
    "error_code": "product_mapping_missing",
    "observed_at": datetime(2026, 10, 21, 12, 0, tzinfo=UTC),
}


def _run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], rows: object, *args: str
) -> tuple[int, str, str]:
    monkeypatch.setenv("APPSELL_RECONCILIATION_DATABASE_URL", DATABASE_URL)
    captured: dict[str, object] = {}

    def fake_read(database_url: str, **window: object) -> object:
        captured.update(database_url=database_url, **window)
        if isinstance(rows, Exception):
            raise rows
        return rows

    monkeypatch.setattr(webhook_reconciliation, "read_divergences", fake_read)
    with pytest.raises(SystemExit) as exit_info:
        webhook_reconciliation.main(["--since", SINCE, "--before", BEFORE, *args])
    out, err = capsys.readouterr()
    return int(exit_info.value.code or 0), out, err


def test_exits_zero_with_an_empty_report(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = _run(monkeypatch, capsys, [])

    assert code == 0
    assert json.loads(out) == {
        "schema_version": 1,
        "window": {"since": "2026-10-20T00:00:00+00:00", "before": "2026-10-23T00:00:00+00:00"},
        "counts": {},
        "by_seller": {},
        "divergences": [],
    }
    assert err == ""


def test_exits_one_and_logs_one_line_per_divergence(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = _run(monkeypatch, capsys, [DIVERGENCE])

    assert code == 1
    report = json.loads(out)
    assert report["counts"] == {"fulfillment_dead_letter": 1}
    assert report["by_seller"] == {"loja-a": 1}
    assert report["divergences"][0]["reference_id"] == "ee000000-0000-4000-8000-0000000000a2"
    assert report["divergences"][0]["observed_at"] == "2026-10-21T12:00:00+00:00"
    lines = [json.loads(line) for line in err.splitlines()]
    assert lines == [{"marker": "appsell.reconciliation.divergence", **report["divergences"][0]}]


def test_passes_the_window_and_stale_limit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[dict[str, object]] = []

    def fake_read(database_url: str, **window: object) -> list[object]:
        calls.append(window)
        return []

    monkeypatch.setenv("APPSELL_RECONCILIATION_DATABASE_URL", DATABASE_URL)
    monkeypatch.setattr(webhook_reconciliation, "read_divergences", fake_read)
    with pytest.raises(SystemExit):
        webhook_reconciliation.main(
            ["--since", "2026-10-20T03:00:00-03:00", "--before", BEFORE, "--stale-minutes", "15"]
        )
    capsys.readouterr()

    assert calls[0]["since"] == datetime(2026, 10, 20, 6, 0, tzinfo=UTC)
    assert str(calls[0]["stale_after"]) == "0:15:00"


def test_fails_with_code_two_without_echoing_the_database_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    error = psycopg.errors.InvalidParameterValue("reconciliation window is invalid")
    code, out, err = _run(monkeypatch, capsys, error)

    assert code == 2
    assert out == ""
    assert "reconciliation report failed" in err
    assert "secret-value" not in err


def test_requires_the_database_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("APPSELL_RECONCILIATION_DATABASE_URL", raising=False)
    with pytest.raises(SystemExit) as exit_info:
        webhook_reconciliation.main(["--since", SINCE, "--before", BEFORE])
    assert exit_info.value.code == 2
    assert "APPSELL_RECONCILIATION_DATABASE_URL is required" in capsys.readouterr().err


@pytest.mark.parametrize("instant", ["2026-10-20T00:00:00", "ontem", ""])
def test_refuses_an_instant_without_timezone_or_unreadable(
    instant: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        webhook_reconciliation.main(["--since", instant, "--before", BEFORE])
    assert exit_info.value.code == 2
    capsys.readouterr()
