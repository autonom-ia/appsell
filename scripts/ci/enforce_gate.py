#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from typing import Mapping


KNOWN_STEPS = {
    "route",
    "audit_api",
    "fast_web",
    "fast_api",
    "fast_backup",
    "fast_preview",
    "cert_web",
    "cert_api",
    "cert_postgres",
    "cert_storage",
    "cert_openbao",
    "cert_backup",
    "cert_preview",
    "image_web",
    "image_api",
    "image_bootstrap",
    "smoke",
}


def expected_steps(
    event: str,
    route: Mapping[str, object],
    *,
    merge_queue_active: bool = False,
) -> set[str]:
    expected = {"route"}
    if event == "pull_request":
        if route.get("api"):
            expected.add("audit_api")
        if route.get("web"):
            expected.add("fast_web")
        if route.get("api"):
            expected.add("fast_api")
        if route.get("backup"):
            expected.add("fast_backup")
        if route.get("preview"):
            expected.add("fast_preview")
    elif event == "push" and merge_queue_active:
        expected.add("smoke")
    elif event in {"merge_group", "push", "workflow_dispatch"}:
        if route.get("api") or route.get("postgres"):
            expected.add("audit_api")
        if route.get("web"):
            expected.add("cert_web")
        if route.get("api"):
            expected.add("cert_api")
        if route.get("postgres"):
            expected.add("cert_postgres")
        if route.get("openbao"):
            expected.add("cert_openbao")
        if route.get("storage"):
            expected.add("cert_storage")
        if route.get("backup"):
            expected.add("cert_backup")
        if route.get("preview"):
            expected.add("cert_preview")
        for scope in ("web", "api", "bootstrap"):
            if route.get(f"image_{scope}"):
                expected.add(f"image_{scope}")
        if event == "push":
            expected.add("smoke")
    else:
        expected.update(KNOWN_STEPS - {"route"})
    return expected


def evaluate_gate(
    event: str,
    route: Mapping[str, object],
    outcomes: Mapping[str, str],
    *,
    merge_queue_active: bool = False,
) -> list[str]:
    expected = expected_steps(event, route, merge_queue_active=merge_queue_active)
    failures: list[str] = []
    for step in sorted(KNOWN_STEPS):
        outcome = outcomes.get(step, "missing")
        if step in expected:
            if outcome != "success":
                failures.append(f"{step}={outcome}")
        elif outcome == "success":
            failures.append(f"{step}=unexpected-success")
        elif outcome not in {"skipped", "missing"}:
            failures.append(f"{step}=unexpected-{outcome}")
    return failures


def main() -> int:
    route_raw = os.environ.get("CI_ROUTE_JSON", "")
    try:
        route = json.loads(route_raw)
    except json.JSONDecodeError:
        print("::error::ci-gate received invalid route JSON")
        return 1

    event = os.environ.get("CI_EVENT", "")
    merge_queue_active = os.environ.get("CI_MERGE_QUEUE_ACTIVE", "") == "true"
    outcomes = {
        step: os.environ.get(f"CI_OUTCOME_{step.upper()}", "missing")
        for step in KNOWN_STEPS
    }
    failures = evaluate_gate(
        event,
        route,
        outcomes,
        merge_queue_active=merge_queue_active,
    )
    for failure in failures:
        print(f"::error::ci-gate rejected {failure}")
    if failures:
        return 1
    print(
        "ci-gate passed for "
        f"event={event} expected={sorted(expected_steps(event, route, merge_queue_active=merge_queue_active))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
