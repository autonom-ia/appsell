#!/usr/bin/env python3
"""Fail-closed PostgreSQL stability gate for disposable capacity runs."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psycopg

MAX_ATTEMPTS = 5
MAX_TRANSIENT_FAILURES = 2
REQUIRED_CONSECUTIVE_SUCCESSES = 3
DEADLINE_SECONDS = 10.0
PROBE_INTERVAL_SECONDS = 1.0
FAULT_MODE = "postgres_restart_once_after_health_v1"
FAULT_CONFIRMATION = "confirm_disposable_postgres_restart_v1"
LOG_EVENT_PATTERNS = {
    "ready": re.compile(r"database system is ready to accept connections", re.I),
    "shutdown": re.compile(
        r"(?:database system is shutting down|received (?:fast|smart|immediate) shutdown request)",
        re.I,
    ),
    "starting": re.compile(r"database system is starting up", re.I),
    "interrupted": re.compile(r"database system was interrupted", re.I),
}


class GateFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        f"{json.dumps(value, indent=2, sort_keys=True)}\n", encoding="utf-8"
    )
    temporary.replace(path)


def classify_postgres_logs(content: str) -> dict[str, int]:
    events = {
        event: len(pattern.findall(content))
        for event, pattern in LOG_EVENT_PATTERNS.items()
    }
    events["capture_errors"] = 0
    return events


def postgres_log_events(container_id: str) -> dict[str, int]:
    try:
        result = subprocess.run(
            ["docker", "logs", "--tail", "200", container_id],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, result.args)
        return classify_postgres_logs(f"{result.stdout}\n{result.stderr}")
    except (OSError, subprocess.SubprocessError):
        return {
            "ready": -1,
            "shutdown": -1,
            "starting": -1,
            "interrupted": -1,
            "capture_errors": 1,
        }


def container_state(project: str) -> dict[str, Any]:
    container_id = compose_output(project, "ps", "--all", "--quiet", "postgres")
    if not container_id:
        raise GateFailure("postgres_container_missing")
    state = docker_output(container_id, "{{.State.Status}}")
    health = docker_output(
        container_id, "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}"
    )
    restart_count = int(docker_output(container_id, "{{.RestartCount}}") or "0")
    return {
        "container_id": container_id,
        "status": state,
        "health": health,
        "restart_count": restart_count,
    }


def validate_scope(project: str, dsn: str, expected_port: int) -> dict[str, Any]:
    if not re.fullmatch(r"appsell_capacity_(?:startup_)?[0-9]+_[0-9]+", project):
        raise GateFailure("unsafe_compose_project")
    environment = os.environ.get("APPSELL_API_ENVIRONMENT", "")
    if environment not in {"local", "ci"}:
        raise GateFailure("unsafe_environment")
    parsed = urlparse(dsn.replace("postgresql+psycopg://", "postgresql://", 1))
    if not parsed.hostname or not ipaddress.ip_address(parsed.hostname).is_loopback:
        raise GateFailure("database_not_loopback")
    if parsed.port != expected_port:
        raise GateFailure("database_port_mismatch")
    state = container_state(project)
    project_label = docker_output(
        state["container_id"], '{{index .Config.Labels "com.docker.compose.project"}}'
    )
    service_label = docker_output(
        state["container_id"], '{{index .Config.Labels "com.docker.compose.service"}}'
    )
    if project_label != project or service_label != "postgres":
        raise GateFailure("container_scope_mismatch")
    return state


def maybe_inject_restart(
    project: str, initial: dict[str, Any], result: dict[str, Any] | None = None
) -> dict[str, Any]:
    mode = os.environ.get("APPSELL_CAPACITY_FAULT_INJECTION", "none")
    if result is None:
        result = {}
    result.update(
        {
            "mode": mode,
            "requested": mode != "none",
            "occurred": False,
            "shutdown_observed": False,
            "restart_observed": False,
            "restart_count_before": initial["restart_count"],
        }
    )
    if mode == "none":
        return result
    if mode != FAULT_MODE:
        raise GateFailure("unknown_fault_injection")
    if os.environ.get("APPSELL_CAPACITY_FAULT_CONFIRMATION") != FAULT_CONFIRMATION:
        raise GateFailure("fault_injection_not_confirmed")
    subprocess.run(
        ["docker", "exec", initial["container_id"], "sh", "-c", "kill -TERM 1"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
    )
    result["occurred"] = True
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        current = container_state(project)
        if current["status"] != "running" or current["health"] != "healthy":
            result["shutdown_observed"] = True
            return result
        time.sleep(0.02)
    raise GateFailure("fault_shutdown_not_observed")


def capture_compose_failure(
    project: str, report_path: Path, dsn: str, expected_port: int
) -> int:
    report: dict[str, Any] = {
        "schema_version": 1,
        "phase": "postgres_startup",
        "status": "failed",
        "code": "compose_wait_failed",
        "calibration_eligible": False,
        "fault_injection": {
            "mode": os.environ.get("APPSELL_CAPACITY_FAULT_INJECTION", "none"),
            "requested": os.environ.get("APPSELL_CAPACITY_FAULT_INJECTION", "none")
            != "none",
            "occurred": False,
        },
    }
    try:
        state = validate_scope(project, dsn, expected_port)
        report["container"] = {
            "status": state["status"],
            "health": state["health"],
            "restart_count": state["restart_count"],
            "log_events": postgres_log_events(state["container_id"]),
        }
    except (GateFailure, OSError, ValueError, subprocess.SubprocessError):
        report["container"] = {
            "status": "unavailable",
            "health": "unavailable",
            "restart_count": -1,
            "log_events": {"capture_errors": 1},
        }
    atomic_json(report_path, report)
    return 1


def run_gate(project: str, report_path: Path, dsn: str, expected_port: int) -> int:
    connect_dsn = dsn.replace("postgresql+psycopg://", "postgresql://", 1)
    started = time.monotonic()
    attempts: list[dict[str, Any]] = []
    transient_failures = 0
    consecutive_successes = 0
    fault_mode = os.environ.get("APPSELL_CAPACITY_FAULT_INJECTION", "none")
    fault = {
        "mode": fault_mode,
        "requested": fault_mode != "none",
        "occurred": False,
        "shutdown_observed": False,
        "restart_observed": False,
        "restart_count_before": 0,
    }
    report: dict[str, Any] = {
        "schema_version": 1,
        "phase": "postgres_startup",
        "status": "failed",
        "code": "startup_gate_failed",
        "attempts": attempts,
        "attempt_count": 0,
        "transient_failures": 0,
        "max_attempts": MAX_ATTEMPTS,
        "max_transient_failures": MAX_TRANSIENT_FAILURES,
        "required_consecutive_successes": REQUIRED_CONSECUTIVE_SUCCESSES,
        "deadline_seconds": DEADLINE_SECONDS,
        "recovered": False,
        "fault_injection": fault,
        "calibration_eligible": False,
    }
    previous_handler = signal.getsignal(signal.SIGALRM)

    def deadline_reached(_signum: int, _frame: object) -> None:
        raise GateFailure("startup_deadline_exceeded")

    signal.signal(signal.SIGALRM, deadline_reached)
    signal.setitimer(signal.ITIMER_REAL, DEADLINE_SECONDS)
    try:
        initial = validate_scope(project, dsn, expected_port)
        fault["restart_count_before"] = initial["restart_count"]
        maybe_inject_restart(project, initial, fault)
        deadline = started + DEADLINE_SECONDS
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if time.monotonic() >= deadline:
                raise GateFailure("startup_deadline_exceeded")
            try:
                current_state = container_state(project)
                if (
                    current_state["status"] != "running"
                    or current_state["health"] != "healthy"
                ):
                    raise psycopg.OperationalError("transient container state")
                with psycopg.connect(
                    connect_dsn,
                    connect_timeout=2,
                    options="-c statement_timeout=2000",
                ) as connection:
                    with connection.cursor() as cursor:
                        cursor.execute("select 1")
                        if cursor.fetchone() != (1,):
                            raise GateFailure("readiness_probe_invalid")
                consecutive_successes += 1
                attempts.append({"attempt": attempt, "status": "ready"})
            except GateFailure:
                raise
            except psycopg.OperationalError as error:
                if getattr(error, "sqlstate", None) in {"28P01", "28000"}:
                    raise GateFailure("readiness_auth_failed") from None
                transient_failures += 1
                consecutive_successes = 0
                attempts.append({"attempt": attempt, "status": "transient_unavailable"})
                if transient_failures > MAX_TRANSIENT_FAILURES:
                    raise GateFailure("transient_failure_budget_exhausted") from None
            if consecutive_successes >= REQUIRED_CONSECUTIVE_SUCCESSES:
                final_state = container_state(project)
                if (
                    final_state["status"] != "running"
                    or final_state["health"] != "healthy"
                ):
                    raise GateFailure("container_not_stable_after_probes")
                fault["restart_observed"] = (
                    final_state["restart_count"] > fault["restart_count_before"]
                )
                if fault["occurred"] and not fault["restart_observed"]:
                    raise GateFailure("fault_restart_not_observed")
                report.update(
                    {
                        "status": "passed",
                        "code": "ready_stable",
                        "attempt_count": len(attempts),
                        "transient_failures": transient_failures,
                        "recovered": transient_failures > 0 or fault["occurred"],
                        "container": {
                            "status": final_state["status"],
                            "health": final_state["health"],
                            "restart_count": final_state["restart_count"],
                            "log_events": postgres_log_events(
                                final_state["container_id"]
                            ),
                        },
                        "calibration_eligible": transient_failures == 0
                        and not fault["occurred"],
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                    }
                )
                atomic_json(report_path, report)
                return 0
            time.sleep(PROBE_INTERVAL_SECONDS)
        raise GateFailure("consecutive_success_budget_exhausted")
    except GateFailure as error:
        report.update(
            {
                "code": error.code,
                "attempt_count": len(attempts),
                "transient_failures": transient_failures,
                "recovered": False,
                "calibration_eligible": False,
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
        )
        try:
            final_state = container_state(project)
            report["container"] = {
                "status": final_state["status"],
                "health": final_state["health"],
                "restart_count": final_state["restart_count"],
                "log_events": postgres_log_events(final_state["container_id"]),
            }
        except (GateFailure, ValueError, subprocess.SubprocessError):
            report["container"] = {
                "status": "unavailable",
                "health": "unavailable",
                "restart_count": -1,
            }
        atomic_json(report_path, report)
        return 1
    except (OSError, ValueError, psycopg.Error, subprocess.SubprocessError):
        report.update(
            {
                "code": "startup_gate_internal_error",
                "attempt_count": len(attempts),
                "transient_failures": transient_failures,
                "recovered": False,
                "calibration_eligible": False,
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
        )
        report["container"] = {
            "status": "unavailable",
            "health": "unavailable",
            "restart_count": -1,
        }
        atomic_json(report_path, report)
        return 1
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def compose_output(project: str, *arguments: str) -> str:
    result = subprocess.run(
        ["docker", "compose", "-p", project, *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result.stdout.strip()


def docker_output(container_id: str, template: str) -> str:
    result = subprocess.run(
        ["docker", "inspect", "--format", template, container_id],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--capture-compose-failure", action="store_true")
    arguments = parser.parse_args()
    dsn = os.environ.get("APPSELL_MIGRATOR_DATABASE_URL", "")
    try:
        port = int(os.environ.get("APPSELL_POSTGRES_PORT", "0"))
    except ValueError:
        port = 0
    if not dsn or not port:
        atomic_json(
            arguments.report,
            {
                "schema_version": 1,
                "phase": "postgres_startup",
                "status": "failed",
                "code": "startup_configuration_missing",
                "calibration_eligible": False,
            },
        )
        return 1
    if arguments.capture_compose_failure:
        return capture_compose_failure(arguments.project, arguments.report, dsn, port)
    return run_gate(arguments.project, arguments.report, dsn, port)


if __name__ == "__main__":
    raise SystemExit(main())
