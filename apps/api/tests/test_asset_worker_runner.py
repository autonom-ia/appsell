from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from sqlalchemy.exc import DBAPIError

from appsell_api import asset_worker_runner
from appsell_api.internal_requests import InternalRequestAuthenticator
from appsell_api.pdf_verifier import PdfSandboxUnavailable
from appsell_api.storage_s3 import StoredObject
from appsell_api.storage_worker import VerifiedAssetResult, VerifiedStoredPdfPage
from appsell_api.video_verifier import VideoSandboxUnavailable


class FakeSession:
    def __init__(self, events: list[str], *, fail_finalize: int = 0, fail_register: int = 0):
        self.events = events
        self.fail_finalize = fail_finalize
        self.fail_register = fail_register
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def scalar(self, statement: Any, parameters: dict[str, Any]) -> Any:
        sql = str(statement)
        self.calls.append((sql, parameters))
        if "claim_asset_verification_jobs" in sql:
            self.events.append("claim")
            return []
        if "claim_asset_storage_cleanup_jobs" in sql:
            self.events.append("claim-cleanup")
            return []
        if "complete_asset_storage_cleanup" in sql:
            self.events.append("complete-cleanup")
            return True
        if "retry_asset_storage_cleanup" in sql:
            self.events.append("retry-cleanup")
            return "queued"
        if "begin_asset_verification" in sql:
            self.events.append("begin")
            return {
                "quarantine_bucket": "appsell-quarantine",
                "ready_bucket": "appsell-ready",
                "quarantine_key": f"tenants/{SELLER}/quarantine/{UPLOAD}",
                "ready_key": f"tenants/{SELLER}/assets/{uuid4()}/v1/{uuid4()}",
                "source_version_id": "source-v1",
                "expected_byte_size": 128,
                "expected_sha256": "ab" * 32,
                "expected_media_type": "application/pdf",
                "file_name": "material.pdf",
            }
        if "finalize_asset_verification" in sql:
            self.events.append("finalize")
            if self.fail_finalize:
                self.fail_finalize -= 1
                raise DBAPIError("finalize", {}, RuntimeError("injected commit uncertainty"))
            return {"status": "ready"}
        if "register_asset_ready_candidate" in sql:
            self.events.append("register-ready")
            if self.fail_register:
                self.fail_register -= 1
                raise DBAPIError("register", {}, RuntimeError("injected commit uncertainty"))
            return {
                "registered": True,
                **(
                    {"pdf_manifest_sha256": "12" * 32}
                    if "register_asset_ready_candidate_v2" in sql
                    else {}
                ),
            }
        raise AssertionError(sql)


class FakeAuth:
    def __init__(self, session: FakeSession):
        self.session = session
        self.contexts: list[tuple[str, UUID | None]] = []

    @asynccontextmanager
    async def trusted_domain_session(self, envelope: Any) -> AsyncIterator[FakeSession]:
        self.contexts.append((envelope.capability, envelope.seller_organization_id))
        yield self.session


class FakeWorker:
    events: list[str] = []
    result: VerifiedAssetResult

    def __init__(self, _storage: Any, _scanner: Any):
        pass

    def process(self, _job: Any) -> VerifiedAssetResult:
        self.events.append("process")
        return self.result

    def cleanup_source_after_commit(self, _result: VerifiedAssetResult) -> None:
        self.events.append("cleanup")

    def compensate_ready_after_finalize_failure(self, _result: VerifiedAssetResult) -> None:
        self.events.append("compensate")


class UnavailableParserWorker(FakeWorker):
    def process(self, _job: Any) -> VerifiedAssetResult:
        self.events.append("process")
        raise PdfSandboxUnavailable("injected sidecar outage")


class UnavailableVideoProbeWorker(FakeWorker):
    def process(self, _job: Any) -> VerifiedAssetResult:
        self.events.append("process")
        raise VideoSandboxUnavailable("injected video probe outage")


class FakeStorage:
    def __init__(self, events: list[str], *, fail_delete: bool = False):
        self.events = events
        self.fail_delete = fail_delete

    def delete_version(self, **_kwargs: Any) -> None:
        self.events.append("delete-version")
        if self.fail_delete:
            raise OSError("injected storage failure")

    def delete_all_versions(self, **_kwargs: Any) -> int:
        self.events.append("delete-all-versions")
        return 1

    def delete_all_versions_under_prefix(self, **_kwargs: Any) -> int:
        self.events.append("delete-all-versions-under-prefix")
        return 2


SELLER = UUID("52000000-0000-4000-8000-000000000001")
UPLOAD = UUID("59000000-0000-4000-8000-000000000001")
WORKER_ID = UUID("51000000-0000-4000-8000-000000000099")


def stored(bucket: str, key: str, version: str) -> StoredObject:
    return StoredObject(
        bucket=bucket,
        key=key,
        version_id=version,
        size=128,
        content_type="application/pdf",
        checksum_sha256_b64="checksum",
        etag="etag",
        appsell_upload_id=str(UPLOAD),
    )


def build_app(
    events: list[str], fail_finalize: int, fail_register: int = 0
) -> tuple[FastAPI, FakeAuth]:
    source = stored("appsell-quarantine", f"tenants/{SELLER}/quarantine/{UPLOAD}", "s1")
    ready = stored("appsell-ready", f"tenants/{SELLER}/assets/a/v1/b", "r1")
    FakeWorker.events = events
    FakeWorker.result = VerifiedAssetResult(
        source=source,
        ready=ready,
        source_byte_size=128,
        source_sha256_hex="ab" * 32,
        ready_byte_size=120,
        ready_sha256_hex="cd" * 32,
        pdf_page_count=1,
        scanner_provider="deterministic-local",
    )
    auth = FakeAuth(FakeSession(events, fail_finalize=fail_finalize, fail_register=fail_register))
    app = FastAPI()
    app.state.settings = SimpleNamespace(
        environment="local",
        identity_issuer="https://identity.test.autonomia.site",
        internal_issuer="appsell-bff",
        internal_audience="appsell-api",
    )
    app.state.internal_auth = auth
    app.state.storage = FakeStorage(events)
    app.state.asset_scanner = object()
    return app, auth


@pytest.mark.asyncio
async def test_pdf_ready_uses_v2_manifest_contract() -> None:
    events: list[str] = []
    app, auth = build_app(events, fail_finalize=0)
    page_object = StoredObject(
        bucket="appsell-ready",
        key=f"tenants/{SELLER}/assets/a/v1/pages/1.png",
        version_id="p1",
        size=64,
        content_type="image/png",
        checksum_sha256_b64="checksum",
        etag="page-etag",
        appsell_upload_id=str(UPLOAD),
    )
    result = VerifiedAssetResult(
        **{
            **FakeWorker.result.__dict__,
            "pdf_pages": (
                VerifiedStoredPdfPage(
                    page_number=1,
                    stored=page_object,
                    byte_size=64,
                    sha256_hex="ef" * 32,
                    width=800,
                    height=1200,
                ),
            ),
        }
    )

    manifest_sha = await asset_worker_runner._register_ready_candidate(  # noqa: SLF001
        cast(InternalRequestAuthenticator, auth),
        app.state.settings,
        WORKER_ID,
        SELLER,
        UPLOAD,
        result,
    )
    await asset_worker_runner._finalize_ready(  # noqa: SLF001
        cast(InternalRequestAuthenticator, auth),
        app.state.settings,
        WORKER_ID,
        SELLER,
        UPLOAD,
        result,
        manifest_sha,
    )

    register_sql, register_parameters = auth.session.calls[-2]
    finalize_sql, finalize_parameters = auth.session.calls[-1]
    assert "register_asset_ready_candidate_v2" in register_sql
    assert '"object_key":"tenants/' in register_parameters["pdf_pages"]
    assert "finalize_asset_verification_v2" in finalize_sql
    assert finalize_parameters["pdf_manifest_sha256"] == "12" * 32


@pytest.mark.asyncio
async def test_worker_finalizes_database_before_source_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    app, auth = build_app(events, fail_finalize=0)
    monkeypatch.setattr(asset_worker_runner, "PdfStorageWorker", FakeWorker)
    monkeypatch.setattr(asset_worker_runner, "_worker_id", lambda _settings: WORKER_ID)

    await asset_worker_runner.process_asset_upload(app, SELLER, UPLOAD)

    assert events == ["begin", "process", "register-ready", "finalize", "cleanup"]
    assert all(
        capability == "catalog.asset.verify" and seller is None
        for capability, seller in auth.contexts
    )


@pytest.mark.asyncio
async def test_ambiguous_finalize_replays_same_receipt_before_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    app, _auth = build_app(events, fail_finalize=1)
    monkeypatch.setattr(asset_worker_runner, "PdfStorageWorker", FakeWorker)
    monkeypatch.setattr(asset_worker_runner, "_worker_id", lambda _settings: WORKER_ID)

    await asset_worker_runner.process_asset_upload(app, SELLER, UPLOAD)

    assert events == [
        "begin",
        "process",
        "register-ready",
        "finalize",
        "finalize",
        "cleanup",
    ]
    assert "compensate" not in events


@pytest.mark.asyncio
async def test_unresolved_commit_uncertainty_preserves_ready_and_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    app, _auth = build_app(events, fail_finalize=2)
    monkeypatch.setattr(asset_worker_runner, "PdfStorageWorker", FakeWorker)
    monkeypatch.setattr(asset_worker_runner, "_worker_id", lambda _settings: WORKER_ID)

    await asset_worker_runner.process_asset_upload(app, SELLER, UPLOAD)

    assert events == ["begin", "process", "register-ready", "finalize", "finalize"]
    assert "cleanup" not in events and "compensate" not in events


@pytest.mark.asyncio
async def test_durable_claim_uses_null_tenant_worker_context() -> None:
    events: list[str] = []
    app, auth = build_app(events, fail_finalize=0)

    claimed = await asset_worker_runner._claim_jobs(  # noqa: SLF001
        cast(InternalRequestAuthenticator, auth), app.state.settings, WORKER_ID
    )

    assert claimed == []
    assert events == ["claim"]
    assert auth.contexts == [("catalog.asset.verify", None)]
    assert auth.session.calls[-1][1]["job_limit"] == 1
    # Deriva da constante: repetir o numero aqui so faz o teste quebrar quando
    # o lease muda de propósito, sem proteger nada.
    assert auth.session.calls[-1][1]["lease_seconds"] == asset_worker_runner._ASSET_LEASE_SECONDS


@pytest.mark.asyncio
async def test_cleanup_deletes_exact_version_before_marking_job_complete() -> None:
    events: list[str] = []
    app, auth = build_app(events, fail_finalize=0)
    cleanup_id = uuid4()

    await asset_worker_runner._process_cleanup_job(  # noqa: SLF001
        app,
        cast(InternalRequestAuthenticator, auth),
        app.state.settings,
        WORKER_ID,
        {
            "id": cleanup_id,
            "seller_organization_id": SELLER,
            "storage_driver": "s3",
            "bucket": "appsell-quarantine",
            "object_key": f"tenants/{SELLER}/quarantine/{UPLOAD}",
            "storage_version_id": "source-v1",
        },
    )

    assert events == ["delete-version", "complete-cleanup"]


@pytest.mark.asyncio
async def test_cleanup_storage_failure_records_durable_retry() -> None:
    events: list[str] = []
    app, auth = build_app(events, fail_finalize=0)
    app.state.storage = FakeStorage(events, fail_delete=True)

    await asset_worker_runner._process_cleanup_job(  # noqa: SLF001
        app,
        cast(InternalRequestAuthenticator, auth),
        app.state.settings,
        WORKER_ID,
        {
            "id": uuid4(),
            "seller_organization_id": SELLER,
            "storage_driver": "s3",
            "bucket": "appsell-quarantine",
            "object_key": f"tenants/{SELLER}/quarantine/{UPLOAD}",
            "storage_version_id": "source-v1",
        },
    )

    assert events == ["delete-version", "retry-cleanup"]


@pytest.mark.asyncio
async def test_cleanup_reconcile_deletes_all_versions_for_unique_quarantine_key() -> None:
    events: list[str] = []
    app, auth = build_app(events, fail_finalize=0)

    await asset_worker_runner._process_cleanup_job(  # noqa: SLF001
        app,
        cast(InternalRequestAuthenticator, auth),
        app.state.settings,
        WORKER_ID,
        {
            "id": uuid4(),
            "seller_organization_id": SELLER,
            "storage_driver": "s3",
            "delete_mode": "delete_all_versions",
            "bucket": "appsell-quarantine",
            "object_key": f"tenants/{SELLER}/quarantine/{UPLOAD}",
            "storage_version_id": None,
        },
    )

    assert events == ["delete-all-versions", "complete-cleanup"]


@pytest.mark.asyncio
async def test_ready_reconcile_deletes_entire_reserved_asset_version_prefix() -> None:
    events: list[str] = []
    app, auth = build_app(events, fail_finalize=0)
    asset_id = uuid4()
    blob_id = uuid4()

    await asset_worker_runner._process_cleanup_job(  # noqa: SLF001
        app,
        cast(InternalRequestAuthenticator, auth),
        app.state.settings,
        WORKER_ID,
        {
            "id": uuid4(),
            "seller_organization_id": SELLER,
            "storage_driver": "s3",
            "kind": "ready_reconcile",
            "delete_mode": "delete_all_versions",
            "bucket": "appsell-ready",
            "object_key": f"tenants/{SELLER}/assets/{asset_id}/v1/{blob_id}",
            "storage_version_id": None,
        },
    )

    assert events == ["delete-all-versions-under-prefix", "complete-cleanup"]


@pytest.mark.asyncio
async def test_ready_reconcile_never_deletes_a_different_tenant_prefix() -> None:
    events: list[str] = []
    app, auth = build_app(events, fail_finalize=0)

    await asset_worker_runner._process_cleanup_job(  # noqa: SLF001
        app,
        cast(InternalRequestAuthenticator, auth),
        app.state.settings,
        WORKER_ID,
        {
            "id": uuid4(),
            "seller_organization_id": SELLER,
            "storage_driver": "s3",
            "kind": "ready_reconcile",
            "delete_mode": "delete_all_versions",
            "bucket": "appsell-ready",
            "object_key": f"tenants/{uuid4()}/assets/{uuid4()}/v1/{uuid4()}",
            "storage_version_id": None,
        },
    )

    assert events == ["retry-cleanup"]


@pytest.mark.asyncio
async def test_ready_registration_failure_compensates_exact_ready_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    app, _auth = build_app(events, fail_finalize=0, fail_register=2)
    monkeypatch.setattr(asset_worker_runner, "PdfStorageWorker", FakeWorker)
    monkeypatch.setattr(asset_worker_runner, "_worker_id", lambda _settings: WORKER_ID)

    await asset_worker_runner.process_asset_upload(app, SELLER, UPLOAD)

    assert events == [
        "begin",
        "process",
        "register-ready",
        "register-ready",
        "compensate",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("worker_type", [UnavailableParserWorker, UnavailableVideoProbeWorker])
async def test_parser_outage_leaves_job_for_retry_without_rejecting(
    monkeypatch: pytest.MonkeyPatch, worker_type: type[FakeWorker]
) -> None:
    events: list[str] = []
    app, _auth = build_app(events, fail_finalize=0)
    monkeypatch.setattr(asset_worker_runner, "PdfStorageWorker", worker_type)
    monkeypatch.setattr(asset_worker_runner, "_worker_id", lambda _settings: WORKER_ID)

    await asset_worker_runner.process_asset_upload(app, SELLER, UPLOAD)

    assert events == ["begin", "process"]


class _RejectedByDatabase(RuntimeError):
    """O que o psycopg entrega: mensagem do `raise` do banco mais o sqlstate."""

    sqlstate = "22023"

    def __str__(self) -> str:
        return "invalid ready asset candidate\nCONTEXT:  função PL/pgSQL ... linha 15 em RAISE"


class RejectingSession(FakeSession):
    async def scalar(self, statement: Any, parameters: dict[str, Any]) -> Any:
        if "register_asset_ready_candidate" in str(statement):
            self.events.append("register-ready")
            raise DBAPIError("register", {}, _RejectedByDatabase())
        return await super().scalar(statement, parameters)


@pytest.mark.asyncio
async def test_a_recusa_do_banco_no_registro_chega_ao_log(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """O terceiro log mudo do mesmo arquivo, em um dia.

    De 22/08 a 27/08/2026 o banco recusou todo formato fora de PDF e MP4 nesta
    chamada. A excecao morria em `except DBAPIError:` sem uma linha de log: o
    material nunca virava `asset_record`, e a tela dizia "a verificacao esta
    demorando mais que o esperado". Nada, em lugar nenhum, dizia por que.

    O que este teste guarda nao e a correcao daquele defeito — e a condicao para
    que o proximo seja encontrado em minutos.
    """
    events: list[str] = []
    app, auth = build_app(events, fail_finalize=0)
    auth.session = RejectingSession(events)
    monkeypatch.setattr(asset_worker_runner, "PdfStorageWorker", FakeWorker)
    monkeypatch.setattr(asset_worker_runner, "_worker_id", lambda _settings: WORKER_ID)

    await asset_worker_runner.process_asset_upload(app, SELLER, UPLOAD)

    # O log de diagnostico sai no `stderr`: o `stdout` e canal de protocolo
    # — `pdf_verifier` le JSON puro do subprocesso do parser.
    saida = capsys.readouterr().err
    assert "asset_worker_register_retry" in saida
    assert "asset_worker_register_failed" in saida
    assert "invalid ready asset candidate" in saida, "o log nao diz o que o banco recusou"
    assert "22023" in saida, "o log nao diz qual regra do banco foi violada"


def test_o_log_nao_repete_sequencia_longa_que_o_banco_devolveu() -> None:
    """A mensagem do Postgres pode repetir o valor que a violou.

    `invalid input syntax for type uuid: "<valor>"` e parecidas devolvem o
    parametro dentro da frase. Nenhuma credencial passa por estas chamadas, mas
    a regra da casa e nao deixar sequencia longa cair em log — e o sha256 de um
    arquivo do vendedor e uma sequencia longa.
    """

    class _ComValor(RuntimeError):
        sqlstate = "22P02"

        def __str__(self) -> str:
            return 'invalid input syntax: "' + "ab" * 32 + '"'

    campos = asset_worker_runner._database_reason(DBAPIError("s", {}, _ComValor()))

    assert campos["sqlstate"] == "22P02"
    assert "invalid input syntax" in campos["detail"]
    assert "ab" * 32 not in campos["detail"]
    assert "<REDACTED>" in campos["detail"]
