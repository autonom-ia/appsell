from __future__ import annotations

import json

import pytest

from appsell_api.capacity_telemetry import (
    capacity_product_create_trace,
    emit_capacity_product_create_trace,
)


def test_trace_is_strict_opaque_ordinal_and_default_off() -> None:
    assert capacity_product_create_trace("pc-000", enabled=False) is None
    assert capacity_product_create_trace("pc-000", enabled=True) == "pc-000"
    assert capacity_product_create_trace("pc-099", enabled=True) == "pc-099"
    for invalid in ("pc-100", "pc-00", "PC-001", "tenant-001", "pc-001-extra", None):
        assert capacity_product_create_trace(invalid, enabled=True) is None


def test_event_is_allowlisted_json_without_request_material(
    capsys: pytest.CaptureFixture[str],
) -> None:
    emit_capacity_product_create_trace(
        trace="pc-042",
        status_code=201,
        outcome="success",
        total_ms=5_001,
        durations_ms={
            "internal_auth_verification": 10,
            "pool_checkout_replay_apply_context": 20,
            "sql": 4_960,
            "commit_response": 11,
        },
    )
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {
        "schema_version",
        "event",
        "layer",
        "trace",
        "status",
        "outcome",
        "total_ms",
        "late_completion",
        "durations_ms",
    }
    assert payload["trace"] == "pc-042"
    assert payload["late_completion"] is True
    serialized = json.dumps(payload)
    for forbidden in ("url", "body", "cookie", "token", "error", "tenant", "user", "resource"):
        assert forbidden not in serialized.lower()


def test_event_rejects_unknown_stage() -> None:
    with pytest.raises(ValueError, match="stages"):
        emit_capacity_product_create_trace(
            trace="pc-001",
            status_code=500,
            outcome="exception",
            total_ms=1,
            durations_ms={"raw_error": 1},
        )
