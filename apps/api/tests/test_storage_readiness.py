from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import Response, status

import appsell_api.main as main_module
from appsell_api.storage_s3 import StorageCapabilityError


class MutableStorage:
    def __init__(self) -> None:
        self.drifted = False
        self.audit_calls = 0
        self.probe_calls = 0

    def probe_availability(self) -> None:
        self.probe_calls += 1

    def assert_runtime_controls(self, **_kwargs: Any) -> None:
        self.audit_calls += 1
        if self.drifted:
            raise StorageCapabilityError("CORS policy drift")


class HealthyScanner:
    def healthy(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_periodic_control_drift_causes_503_and_next_tick_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 100.0
    storage = MutableStorage()
    settings = SimpleNamespace(
        storage_configured=True,
        storage_allowed_origin="https://sell.autonomia.site",
        storage_control_audit_interval_seconds=60,
        storage_health_probe_interval_seconds=10,
        storage_readiness_stale_after_seconds=30,
        environment="production",
        pdf_sandbox_required=True,
    )
    state = main_module.app.state
    monkeypatch.setattr(state, "settings", settings, raising=False)
    monkeypatch.setattr(state, "storage", storage, raising=False)
    monkeypatch.setattr(state, "asset_scanner", HealthyScanner(), raising=False)
    monkeypatch.setattr(state, "storage_controls_ready", True, raising=False)
    monkeypatch.setattr(state, "storage_runtime_ready", True, raising=False)
    monkeypatch.setattr(state, "storage_controls_checked_at", 0.0, raising=False)
    monkeypatch.setattr(state, "storage_checked_at", now, raising=False)
    monkeypatch.setattr(state, "pdf_sandbox_runtime_ready", True, raising=False)
    monkeypatch.setattr(state, "pdf_sandbox_checked_at", now, raising=False)
    monkeypatch.setattr(state, "db_engine", object(), raising=False)
    monkeypatch.setattr(time, "monotonic", lambda: now)
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)

    async def database_is_ready(_engine: object) -> bool:
        return True

    async def parser_is_ready(_settings: object) -> bool:
        return True

    monkeypatch.setattr(main_module, "database_ready", database_is_ready)
    monkeypatch.setattr(main_module, "_pdf_sandbox_ready", parser_is_ready)

    storage.drifted = True
    await main_module._refresh_storage_dependencies(main_module.app)
    failed_response = Response()
    failed_payload = await main_module.ready(failed_response)

    assert storage.audit_calls == 1
    assert failed_response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert failed_payload["storage"] is False

    now = 110.0
    storage.drifted = False
    await main_module._refresh_storage_dependencies(main_module.app)
    recovered_response = Response()
    recovered_payload = await main_module.ready(recovered_response)

    assert storage.audit_calls == 2
    assert recovered_response.status_code == status.HTTP_200_OK
    assert recovered_payload["storage"] is True

    now = 115.0
    await main_module._refresh_storage_dependencies(main_module.app)
    await main_module.ready(Response())
    await main_module.ready(Response())
    assert storage.audit_calls == 2


@pytest.mark.asyncio
async def test_protected_pdf_sandbox_drift_causes_503_without_s3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 200.0
    settings = SimpleNamespace(
        storage_configured=False,
        storage_readiness_stale_after_seconds=30,
        storage_health_probe_interval_seconds=10,
        environment="ci",
        pdf_sandbox_required=True,
    )
    state = main_module.app.state
    monkeypatch.setattr(state, "settings", settings, raising=False)
    monkeypatch.setattr(state, "storage", None, raising=False)
    monkeypatch.setattr(state, "asset_scanner", None, raising=False)
    monkeypatch.setattr(state, "pdf_sandbox_runtime_ready", True, raising=False)
    monkeypatch.setattr(state, "pdf_sandbox_checked_at", now, raising=False)
    monkeypatch.setattr(state, "db_engine", object(), raising=False)
    monkeypatch.setattr(time, "monotonic", lambda: now)
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)

    async def database_is_ready(_engine: object) -> bool:
        return True

    async def parser_is_not_ready(_settings: object) -> bool:
        return False

    monkeypatch.setattr(main_module, "database_ready", database_is_ready)
    monkeypatch.setattr(main_module, "_pdf_sandbox_ready", parser_is_not_ready)

    await main_module._refresh_storage_dependencies(main_module.app)
    response = Response()
    payload = await main_module.ready(response)

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert payload == {
        "status": "unavailable",
        "database": True,
        "storage": True,
        "pdf_sandbox": False,
    }
