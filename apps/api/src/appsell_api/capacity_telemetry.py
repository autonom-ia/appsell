from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Literal

_TRACE = re.compile(r"^pc-0[0-9]{2}$")
_STAGES = (
    "internal_auth_verification",
    "pool_checkout_replay_apply_context",
    "sql",
    "commit_response",
)
_MAX_DURATION_MS = 900_000.0


def capacity_product_create_trace(value: str | None, *, enabled: bool) -> str | None:
    if not enabled or value is None or _TRACE.fullmatch(value) is None:
        return None
    return value


def emit_capacity_product_create_trace(
    *,
    trace: str,
    status_code: int,
    outcome: Literal["success", "http_error", "exception"],
    total_ms: float,
    durations_ms: Mapping[str, float],
) -> None:
    if _TRACE.fullmatch(trace) is None:
        raise ValueError("invalid capacity product-create trace")
    if set(durations_ms) != set(_STAGES):
        raise ValueError("invalid capacity product-create trace stages")
    rounded_durations = {stage: _duration(durations_ms[stage]) for stage in _STAGES}
    rounded_total = _duration(total_ms)
    if abs(sum(rounded_durations.values()) - rounded_total) > 1:
        raise ValueError("capacity product-create stages do not cover total duration")
    event = {
        "schema_version": 1,
        "event": "appsell_capacity_product_create_trace",
        "layer": "api",
        "trace": trace,
        "status": status_code,
        "outcome": outcome,
        "total_ms": rounded_total,
        "late_completion": rounded_total >= 5_000,
        "durations_ms": rounded_durations,
    }
    print(json.dumps(event, separators=(",", ":"), sort_keys=True), flush=True)


def _duration(value: float) -> float:
    if not 0 <= value <= _MAX_DURATION_MS:
        raise ValueError("invalid capacity product-create duration")
    return round(value, 2)
