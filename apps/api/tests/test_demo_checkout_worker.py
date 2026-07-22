from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import FastAPI
from sqlalchemy.exc import DBAPIError

from appsell_api import demo_checkout_worker
from appsell_api.config import Settings
from appsell_api.internal_requests import InternalRequestAuthenticator

_IDLE = 0.25

INTERNAL_AUTH: dict[str, Any] = {
    "internal_issuer": "appsell-bff",
    "internal_audience": "appsell-api",
    "identity_issuer": "https://identity.test",
    "internal_hmac_keyring_b64_json": json.dumps(
        [{"id": "test-v1", "key": base64.b64encode(bytes(range(32))).decode(), "active": True}]
    ),
    "context_hmac_key_b64": base64.b64encode(bytes(range(32, 64))).decode(),
    "service_credential_b64": base64.b64encode(bytes(range(64, 96))).decode(),
}


class FailingSession:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def scalar(self, statement: Any, parameters: dict[str, Any]) -> Any:
        raise self.error


class FakeAuthenticator:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.claims = 0

    @asynccontextmanager
    async def trusted_domain_session(self, envelope: Any) -> AsyncIterator[FailingSession]:
        self.claims += 1
        yield FailingSession(self.error)


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"environment": "ci", **INTERNAL_AUTH}
    values.update(overrides)
    return Settings(**values)


def _app(settings: Settings, auth: Any) -> FastAPI:
    app = FastAPI()
    app.state.settings = settings
    app.state.internal_auth = cast(InternalRequestAuthenticator | None, auth)
    return app


# --- gate: a flag só desliga, nunca habilita fora de local/ci -----------------


@pytest.mark.parametrize("environment", ["local", "ci"])
def test_worker_active_by_default_in_local_and_ci(environment: str) -> None:
    assert _settings(environment=environment).demo_checkout_worker_active is True


@pytest.mark.parametrize("environment", ["local", "ci"])
def test_flag_disables_worker_in_local_and_ci(environment: str) -> None:
    settings = _settings(environment=environment, demo_checkout_worker_enabled=False)
    assert settings.demo_checkout_worker_active is False


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_flag_true_does_not_enable_worker_outside_local_ci(environment: str) -> None:
    # Fail-closed: mesmo com a flag ligada explicitamente o worker segue desligado.
    settings = Settings.model_construct(
        environment=environment,
        demo_checkout_worker_enabled=True,
    )
    assert settings.demo_checkout_worker_active is False


async def test_loop_returns_immediately_when_disabled() -> None:
    auth = FakeAuthenticator(RuntimeError("must not be called"))
    settings = _settings(demo_checkout_worker_enabled=False)
    app = _app(settings, auth)
    stop = asyncio.Event()

    await asyncio.wait_for(demo_checkout_worker.run_demo_checkout_worker_loop(app, stop), 1)

    assert auth.claims == 0


# --- backoff + log com motivo ------------------------------------------------


async def test_failing_claim_backs_off_and_logs_the_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waits: list[float] = []
    logged: list[dict[str, Any]] = []

    async def fake_wait_for(awaitable: Any, **kwargs: Any) -> None:
        waits.append(kwargs["timeout"])
        awaitable.close()
        if len(waits) >= 4:
            raise asyncio.CancelledError
        raise TimeoutError

    def fake_warning(event: str, **fields: Any) -> None:
        logged.append({"event": event, **fields})

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
    monkeypatch.setattr(demo_checkout_worker.logger, "warning", fake_warning)

    auth = FakeAuthenticator(RuntimeError("claim_demo_checkout_jobs does not exist"))
    app = _app(_settings(), auth)
    stop = asyncio.Event()

    with pytest.raises(asyncio.CancelledError):
        await demo_checkout_worker.run_demo_checkout_worker_loop(app, stop)

    # Backoff cresce a cada falha consecutiva, partindo do intervalo ocioso.
    assert waits == sorted(waits)
    assert waits[0] < waits[-1]
    assert waits[-1] <= demo_checkout_worker._MAX_BACKOFF_SECONDS

    # Cada retry carrega o motivo real, não só o nome do evento.
    assert logged, "worker must log why it is retrying"
    first = logged[0]
    assert first["event"] == "demo_checkout_worker_retry"
    assert first["error_type"] == "RuntimeError"
    assert first["error"] == demo_checkout_worker._OPAQUE_REASON
    assert first["backoff_seconds"] == pytest.approx(waits[0])
    assert first["consecutive_failures"] == 1
    assert logged[1]["consecutive_failures"] == 2


async def test_backoff_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    waits: list[float] = []

    async def fake_wait_for(awaitable: Any, **kwargs: Any) -> None:
        waits.append(kwargs["timeout"])
        awaitable.close()
        if len(waits) >= 40:
            raise asyncio.CancelledError
        raise TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
    monkeypatch.setattr(demo_checkout_worker.logger, "warning", lambda *a, **k: None)

    auth = FakeAuthenticator(RuntimeError("boom"))
    app = _app(_settings(), auth)

    with pytest.raises(asyncio.CancelledError):
        await demo_checkout_worker.run_demo_checkout_worker_loop(app, asyncio.Event())

    assert max(waits) == demo_checkout_worker._MAX_BACKOFF_SECONDS


async def test_backoff_resets_after_a_successful_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    waits: list[float] = []
    state = SimpleNamespace(calls=0)

    async def _empty_claim(statement: Any, parameters: dict[str, Any]) -> Any:
        return []

    class Recovering:
        @asynccontextmanager
        async def trusted_domain_session(self, envelope: Any) -> AsyncIterator[Any]:
            state.calls += 1
            if state.calls <= 3:
                yield FailingSession(RuntimeError("boom"))
            else:
                yield SimpleNamespace(scalar=_empty_claim)

    async def fake_wait_for(awaitable: Any, **kwargs: Any) -> None:
        waits.append(kwargs["timeout"])
        awaitable.close()
        if len(waits) >= 5:
            raise asyncio.CancelledError
        raise TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
    monkeypatch.setattr(demo_checkout_worker.logger, "warning", lambda *a, **k: None)

    app = _app(_settings(), Recovering())

    with pytest.raises(asyncio.CancelledError):
        await demo_checkout_worker.run_demo_checkout_worker_loop(app, asyncio.Event())

    # Após a recuperação o intervalo volta ao ocioso original.
    assert waits[2] > demo_checkout_worker._IDLE_INTERVAL_SECONDS
    assert waits[3] == demo_checkout_worker._IDLE_INTERVAL_SECONDS


async def test_apply_failure_backs_off_instead_of_spinning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_apply` trata DBAPIError internamente; o loop ainda precisa desacelerar."""
    waits: list[float] = []
    logged: list[dict[str, Any]] = []
    job = {
        "id": "6f1d2a58-0000-4000-8000-000000000001",
        "seller_organization_id": "6f1d2a58-0000-4000-8000-000000000002",
        "lease_fence": 1,
    }

    class Disconnected(Exception):
        sqlstate = "08006"

    claims = SimpleNamespace(count=0)

    async def claim_then_fail_apply(statement: Any, parameters: dict[str, Any]) -> Any:
        # O claim entrega um job; o apply e o registro da falha erram no banco,
        # exercitando o `_apply` real e o `except DBAPIError` dele.
        if "claim_demo_checkout_jobs" in str(statement):
            claims.count += 1
            # Guarda de terminação independente do sleep: se o loop voltar a dar
            # `continue` seco, `fake_wait_for` nunca roda e o teste travaria.
            if claims.count > 8:
                raise asyncio.CancelledError
            return [job]
        raise DBAPIError("select ...", {}, Disconnected("connection lost"))

    class ClaimingSession:
        @asynccontextmanager
        async def trusted_domain_session(self, envelope: Any) -> AsyncIterator[Any]:
            yield SimpleNamespace(scalar=claim_then_fail_apply)

    async def fake_wait_for(awaitable: Any, **kwargs: Any) -> None:
        waits.append(kwargs["timeout"])
        awaitable.close()
        if len(waits) >= 3:
            raise asyncio.CancelledError
        raise TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
    monkeypatch.setattr(
        demo_checkout_worker.logger,
        "warning",
        lambda event, **f: logged.append({"event": event, **f}),
    )

    app = _app(_settings(), ClaimingSession())

    with pytest.raises(asyncio.CancelledError):
        await demo_checkout_worker.run_demo_checkout_worker_loop(app, asyncio.Event())

    # Com jobs presentes e apply falhando, o loop não pode dar `continue` seco.
    assert len(waits) >= 2, "apply failure must not skip the sleep"
    assert waits[1] > waits[0], "apply failure must feed the backoff"

    # O `_apply` real reportou a falha do job e devolveu False ao loop.
    assert [entry["event"] for entry in logged].count("demo_checkout_worker_apply_failed") >= 1
    retries = [entry for entry in logged if entry["event"] == "demo_checkout_worker_retry"]
    assert retries[0]["consecutive_failures"] == 1
    assert retries[1]["consecutive_failures"] == 2


async def test_retry_delay_survives_a_permanent_failure() -> None:
    # 2.0 ** 1024 levantaria OverflowError e mataria a task do worker.
    assert demo_checkout_worker._retry_delay(5000) == demo_checkout_worker._MAX_BACKOFF_SECONDS


def test_retry_delay_doubles_until_the_cap() -> None:
    assert demo_checkout_worker._retry_delay(1) == _IDLE
    assert demo_checkout_worker._retry_delay(2) == _IDLE * 2
    assert demo_checkout_worker._retry_delay(3) == _IDLE * 4
    assert demo_checkout_worker._retry_delay(99) == demo_checkout_worker._MAX_BACKOFF_SECONDS


def test_database_errors_never_log_statement_or_parameters() -> None:
    # str(DBAPIError) carrega o SQL e os parâmetros vinculados.
    class UndefinedFunction(Exception):
        sqlstate = "42883"

    error = DBAPIError(
        "select app_private.claim_demo_checkout_jobs(:worker)",
        {"worker": "s3cr3t-value-in-parameters"},
        UndefinedFunction("function does not exist"),
    )

    reason = demo_checkout_worker._sanitized_reason(error)

    assert "claim_demo_checkout_jobs" not in reason
    assert "s3cr3t-value-in-parameters" not in reason
    assert "42883" in reason


@pytest.mark.parametrize(
    "secret",
    [
        # base64 com a pontuação `+` e `/` — uma regex sem esse alfabeto
        # fragmentaria o valor e deixaria os pedaços escaparem.
        base64.b64encode(bytes([255, 239, 190, 251, 239, 190] * 8)).decode(),
        "postgresql+asyncpg://appsell:pa55w0rd@db.internal:5432/appsell",
        "token=AAAAAAAAAAAAAAAAAAAAAAAA",  # 24 chars: curto demais para regex
        "senha123",  # curto e sem formato reconhecível
    ],
)
def test_unknown_messages_never_reach_the_log(secret: str) -> None:
    """Allowlist: mensagem de origem arbitrária é suprimida por inteiro."""
    reason = demo_checkout_worker._sanitized_reason(RuntimeError(f"failed using {secret}"))

    assert reason == demo_checkout_worker._OPAQUE_REASON
    assert secret not in reason


def test_known_messages_survive_for_diagnosis() -> None:
    # Mensagens produzidas por este módulo são seguras e precisam ser legíveis.
    error = RuntimeError("invalid demo checkout claim response")

    assert demo_checkout_worker._sanitized_reason(error) == "invalid demo checkout claim response"


def test_missing_job_field_is_named_only_when_it_is_a_known_field() -> None:
    assert demo_checkout_worker._sanitized_reason(KeyError("lease_fence")) == (
        "missing job field lease_fence"
    )
    assert (
        demo_checkout_worker._sanitized_reason(KeyError("api_token_value"))
        == demo_checkout_worker._OPAQUE_REASON
    )


async def test_arbitrary_error_message_is_suppressed_in_the_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logged: list[dict[str, Any]] = []

    async def fake_wait_for(awaitable: Any, **kwargs: Any) -> None:
        awaitable.close()
        raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
    monkeypatch.setattr(
        demo_checkout_worker.logger, "warning", lambda event, **f: logged.append({**f})
    )

    secret = "A" * 48
    auth = FakeAuthenticator(RuntimeError(f"auth failed for token {secret}"))
    app = _app(_settings(), auth)

    with pytest.raises(asyncio.CancelledError):
        await demo_checkout_worker.run_demo_checkout_worker_loop(app, asyncio.Event())

    # O tipo do erro continua no log; a mensagem, não.
    assert secret not in logged[0]["error"]
    assert logged[0]["error"] == demo_checkout_worker._OPAQUE_REASON
    assert logged[0]["error_type"] == "RuntimeError"
