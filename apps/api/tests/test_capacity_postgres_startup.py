from __future__ import annotations

import importlib.util
import json
import select
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


def load_module() -> ModuleType:
    path = Path(__file__).parents[3] / "scripts" / "capacity_postgres_startup.py"
    spec = importlib.util.spec_from_file_location("capacity_postgres_startup", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


startup = load_module()


def state(restarts: int = 0) -> dict[str, Any]:
    return {
        "container_id": "container-id",
        "status": "running",
        "health": "healthy",
        "restart_count": restarts,
    }


def test_scope_requires_local_ci_loopback_and_matching_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSELL_API_ENVIRONMENT", "ci")
    monkeypatch.setattr(startup, "container_state", lambda _project: state())
    monkeypatch.setattr(
        startup,
        "docker_output",
        lambda _container, template: "wrong" if "project" in template else "postgres",
    )
    with pytest.raises(startup.GateFailure, match="container_scope_mismatch"):
        startup.validate_scope(
            "appsell_capacity_1_2", "postgresql://user@127.0.0.1:55432/appsell", 55432
        )


@pytest.mark.parametrize(
    ("environment", "dsn", "port", "code"),
    [
        ("production", "postgresql://user@127.0.0.1:55432/appsell", 55432, "unsafe_environment"),
        ("ci", "postgresql://user@10.0.0.8:55432/appsell", 55432, "database_not_loopback"),
        ("ci", "postgresql://user@127.0.0.1:55433/appsell", 55432, "database_port_mismatch"),
    ],
)
def test_scope_fails_closed_before_container_inspection(
    monkeypatch: pytest.MonkeyPatch, environment: str, dsn: str, port: int, code: str
) -> None:
    monkeypatch.setenv("APPSELL_API_ENVIRONMENT", environment)
    with pytest.raises(startup.GateFailure, match=code):
        startup.validate_scope("appsell_capacity_1_2", dsn, port)


def test_scope_rejects_non_disposable_compose_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSELL_API_ENVIRONMENT", "ci")
    with pytest.raises(startup.GateFailure, match="unsafe_compose_project"):
        startup.validate_scope("production", "postgresql://user@127.0.0.1:55432/appsell", 55432)


def test_fault_injection_requires_exact_mode_and_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSELL_CAPACITY_FAULT_INJECTION", startup.FAULT_MODE)
    monkeypatch.delenv("APPSELL_CAPACITY_FAULT_CONFIRMATION", raising=False)
    with pytest.raises(startup.GateFailure, match="fault_injection_not_confirmed"):
        startup.maybe_inject_restart("project", state())


def test_atomic_report_never_contains_exception_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = tmp_path / "startup.json"
    monkeypatch.setattr(
        startup,
        "validate_scope",
        lambda *_args: (_ for _ in ()).throw(startup.GateFailure("safe_code")),
    )
    monkeypatch.setattr(startup, "container_state", lambda _project: state())
    assert startup.run_gate("project", report, "postgresql://secret@example.test/db", 5432) == 1
    payload = report.read_text(encoding="utf-8")
    assert "safe_code" in payload
    assert "secret" not in payload
    assert json.loads(payload)["calibration_eligible"] is False


def test_postgres_logs_are_reduced_to_allowlisted_event_counts() -> None:
    content = """
    database system is shutting down
    received fast shutdown request
    database system is ready to accept connections
    postgresql://user:secret@example.test/appsell buyer@autonomia.site
    """
    classified = startup.classify_postgres_logs(content)
    assert classified == {
        "ready": 1,
        "shutdown": 2,
        "starting": 0,
        "interrupted": 0,
        "capture_errors": 0,
    }
    serialized = json.dumps(classified)
    assert "secret" not in serialized
    assert "autonomia.site" not in serialized


def test_postgres_log_command_failure_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        startup.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            args=["docker", "logs"],
            stdout="",
            stderr="daemon unavailable",
        ),
    )
    assert startup.postgres_log_events("container") == {
        "ready": -1,
        "shutdown": -1,
        "starting": -1,
        "interrupted": -1,
        "capture_errors": 1,
    }


class FakeCursor:
    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, _query: str) -> None:
        return None

    def fetchone(self) -> tuple[int]:
        return (1,)


class FakeConnection:
    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return FakeCursor()


def test_gate_recovers_within_fixed_transient_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcomes: list[object] = [
        startup.psycopg.OperationalError("unavailable"),
        startup.psycopg.OperationalError("unavailable"),
        FakeConnection(),
        FakeConnection(),
        FakeConnection(),
    ]
    monkeypatch.setattr(startup, "validate_scope", lambda *_args: state())
    monkeypatch.setattr(
        startup,
        "maybe_inject_restart",
        lambda *_args: {
            "mode": "none",
            "requested": False,
            "occurred": False,
            "shutdown_observed": False,
            "restart_observed": False,
            "restart_count_before": 0,
        },
    )
    monkeypatch.setattr(startup, "container_state", lambda _project: state())
    monkeypatch.setattr(startup.time, "sleep", lambda _seconds: None)

    def connect(*_args: object, **_kwargs: object) -> FakeConnection:
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, FakeConnection)
        return outcome

    monkeypatch.setattr(startup.psycopg, "connect", connect)
    report = tmp_path / "startup.json"
    assert startup.run_gate("project", report, "postgresql://user@127.0.0.1:5432/db", 5432) == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["attempt_count"] == 5
    assert payload["transient_failures"] == 2
    assert payload["recovered"] is True
    assert payload["calibration_eligible"] is False


def test_gate_fails_before_mutation_when_transient_budget_is_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(startup, "validate_scope", lambda *_args: state())
    monkeypatch.setattr(
        startup,
        "maybe_inject_restart",
        lambda *_args: {
            "mode": "none",
            "requested": False,
            "occurred": False,
            "shutdown_observed": False,
            "restart_observed": False,
            "restart_count_before": 0,
        },
    )
    monkeypatch.setattr(startup, "container_state", lambda _project: state())
    monkeypatch.setattr(startup.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        startup.psycopg,
        "connect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(startup.psycopg.OperationalError("down")),
    )
    report = tmp_path / "startup.json"
    assert startup.run_gate("project", report, "postgresql://user@127.0.0.1:5432/db", 5432) == 1
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["code"] == "transient_failure_budget_exhausted"
    assert payload["attempt_count"] == 3
    assert payload["calibration_eligible"] is False


def test_deadline_interrupts_a_probe_and_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(startup, "DEADLINE_SECONDS", 0.05)
    monkeypatch.setattr(startup, "validate_scope", lambda *_args: state())
    monkeypatch.setattr(
        startup,
        "maybe_inject_restart",
        lambda *_args: {
            "mode": "none",
            "requested": False,
            "occurred": False,
            "shutdown_observed": False,
            "restart_observed": False,
            "restart_count_before": 0,
        },
    )
    monkeypatch.setattr(startup, "container_state", lambda _project: state())

    def blocked_connect(*_args: object, **_kwargs: object) -> FakeConnection:
        select.select([], [], [], 1)
        return FakeConnection()

    monkeypatch.setattr(startup.psycopg, "connect", blocked_connect)
    report = tmp_path / "deadline.json"
    assert startup.run_gate("project", report, "postgresql://user@127.0.0.1:5432/db", 5432) == 1
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["code"] == "startup_deadline_exceeded"
    assert payload["elapsed_seconds"] < 0.5


def test_final_container_state_must_remain_healthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0
    monkeypatch.setattr(startup, "validate_scope", lambda *_args: state())
    monkeypatch.setattr(
        startup,
        "maybe_inject_restart",
        lambda *_args: {
            "mode": "none",
            "requested": False,
            "occurred": False,
            "shutdown_observed": False,
            "restart_observed": False,
            "restart_count_before": 0,
        },
    )

    def current_state(_project: str) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return state() if calls <= 3 else {**state(), "status": "exited", "health": "unhealthy"}

    monkeypatch.setattr(startup, "container_state", current_state)
    monkeypatch.setattr(startup.psycopg, "connect", lambda *_args, **_kwargs: FakeConnection())
    monkeypatch.setattr(startup.time, "sleep", lambda _seconds: None)
    report = tmp_path / "final-state.json"
    assert startup.run_gate("project", report, "postgresql://user@127.0.0.1:5432/db", 5432) == 1
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["code"] == "container_not_stable_after_probes"
    assert payload["status"] == "failed"


def test_compose_failure_capture_is_allowlisted_with_available_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(startup, "validate_scope", lambda *_args: state(2))
    monkeypatch.setattr(
        startup,
        "postgres_log_events",
        lambda _container: {
            "ready": 1,
            "shutdown": 1,
            "starting": 0,
            "interrupted": 0,
            "capture_errors": 0,
        },
    )
    report = tmp_path / "compose-failure.json"
    dsn = "postgresql://user:secret@127.0.0.1:5432/appsell"
    assert startup.capture_compose_failure("appsell_capacity_1_2", report, dsn, 5432) == 1
    serialized = report.read_text(encoding="utf-8")
    payload = json.loads(serialized)
    assert payload["status"] == "failed"
    assert payload["code"] == "compose_wait_failed"
    assert payload["calibration_eligible"] is False
    assert payload["container"]["restart_count"] == 2
    assert payload["container"]["log_events"]["shutdown"] == 1
    assert "secret" not in serialized
    assert "postgresql://" not in serialized


def test_compose_failure_capture_remains_safe_when_state_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        startup,
        "validate_scope",
        lambda *_args: (_ for _ in ()).throw(startup.GateFailure("container_missing")),
    )
    report = tmp_path / "compose-unavailable.json"
    assert (
        startup.capture_compose_failure(
            "appsell_capacity_1_2",
            report,
            "postgresql://user:secret@127.0.0.1:5432/appsell",
            5432,
        )
        == 1
    )
    serialized = report.read_text(encoding="utf-8")
    payload = json.loads(serialized)
    assert payload["container"] == {
        "status": "unavailable",
        "health": "unavailable",
        "restart_count": -1,
        "log_events": {"capture_errors": 1},
    }
    assert "secret" not in serialized
