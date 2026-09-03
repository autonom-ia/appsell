"""A4.3 — a capa só é promovida depois de provada, e some quando não é."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import threading
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from PIL import Image

from appsell_api import cover_routes
from appsell_api.storage_s3 import StoredObject
from appsell_api.storage_worker import (
    CleanupRetryableError,
    DeterministicLocalScanner,
    ProductCoverWorker,
    ScannerUnavailable,
    ScanRejected,
    VerifyCoverJob,
    cover_quarantine_key,
    cover_ready_key,
)

SELLER = UUID("11111111-1111-4111-8111-111111111111")
PRODUCT = UUID("22222222-2222-4222-8222-222222222222")
UPLOAD = UUID("33333333-3333-4333-8333-333333333333")


def png(width: int = 320, height: int = 180) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (12, 90, 160)).save(buffer, format="PNG")
    return buffer.getvalue()


def make_job(payload: bytes, *, media_type: str = "image/png") -> VerifyCoverJob:
    return VerifyCoverJob(
        seller_id=SELLER,
        product_id=PRODUCT,
        upload_id=UPLOAD,
        source_bucket="quarantine",
        ready_bucket="ready",
        kind="cover",
        source_key=cover_quarantine_key(SELLER, PRODUCT),
        ready_key=cover_ready_key(SELLER, PRODUCT, UPLOAD),
        expected_size=len(payload),
        expected_sha256_hex=hashlib.sha256(payload).hexdigest(),
        declared_content_type=media_type,
    )


def quarantined(job: VerifyCoverJob, payload: bytes, **overrides: Any) -> StoredObject:
    fields: dict[str, Any] = {
        "bucket": "quarantine",
        "key": job.source_key,
        "version_id": "source-v1",
        "size": len(payload),
        "content_type": job.declared_content_type,
        "checksum_sha256_b64": base64.b64encode(hashlib.sha256(payload).digest()).decode(),
        "etag": "source-etag",
        "appsell_upload_id": str(job.upload_id),
    }
    return StoredObject(**{**fields, **overrides})


class FakeStorage:
    quarantine_bucket = "quarantine"
    ready_bucket = "ready"

    def __init__(self, source: StoredObject, payload: bytes) -> None:
        self.source = source
        self.payload = payload
        self.ready_payload = b""
        self.put_key: str | None = None
        self.deleted_versions: list[tuple[str, str, str]] = []
        self.deleted_keys: list[tuple[str, str]] = []
        self.fail_quarantine_delete = False

    def head(self, **_kwargs: Any) -> StoredObject:
        return self.source

    def open_download(self, **_kwargs: Any) -> Any:
        return io.BytesIO(self.payload)

    def put_cover(
        self,
        *,
        destination_key: str,
        source_path: Path,
        byte_size: int,
        sha256_hex: str,
        content_type: str,
    ) -> StoredObject:
        payload = source_path.read_bytes()
        assert len(payload) == byte_size
        assert hashlib.sha256(payload).hexdigest() == sha256_hex
        self.put_key = destination_key
        self.ready_payload = payload
        return StoredObject(
            bucket=self.ready_bucket,
            key=destination_key,
            version_id="ready-v1",
            size=byte_size,
            content_type=content_type,
            checksum_sha256_b64=base64.b64encode(bytes.fromhex(sha256_hex)).decode(),
            etag="ready-etag",
            appsell_upload_id="",
        )

    def delete_version(self, *, bucket: str, key: str, version_id: str) -> None:
        self.deleted_versions.append((bucket, key, version_id))

    def delete_all_versions(self, *, bucket: str, key: str) -> int:
        if bucket == self.quarantine_bucket and self.fail_quarantine_delete:
            raise RuntimeError("quarantine delete failed")
        self.deleted_keys.append((bucket, key))
        return 1


class RefusingScanner:
    provider_name = "refusing"
    production_capable = False

    def healthy(self) -> bool:
        return True

    def scan(self, _path: Path) -> bool:
        return False


class UnhealthyScanner(RefusingScanner):
    def healthy(self) -> bool:
        return False


def worker(storage: Any, scanner: Any = None) -> ProductCoverWorker:
    return ProductCoverWorker(storage, scanner or DeterministicLocalScanner())


def test_promotes_the_sanitised_bytes_and_leaves_quarantine_for_later() -> None:
    payload = png()
    job = make_job(payload)
    storage = FakeStorage(quarantined(job, payload), payload)

    result = worker(storage).process(job)

    assert storage.put_key == job.ready_key
    assert result.ready.key == job.ready_key
    assert result.width == 320 and result.height == 180
    assert result.sha256_hex == hashlib.sha256(storage.ready_payload).hexdigest()
    # A quarentena so cai depois que o banco gravou. Ate la, e a unica copia.
    assert storage.deleted_keys == []

    worker(storage).cleanup_source_after_commit(result)
    assert storage.deleted_keys == [("quarantine", job.source_key)]


def test_refuses_a_quarantined_object_from_another_attempt() -> None:
    payload = png()
    job = make_job(payload)
    other = quarantined(job, payload, appsell_upload_id=str(uuid4()))
    storage = FakeStorage(other, payload)

    with pytest.raises(ScanRejected, match="another upload attempt"):
        worker(storage).process(job)
    assert storage.put_key is None


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"size": 1}, "size differs"),
        ({"content_type": "image/jpeg"}, "MIME differs"),
        ({"checksum_sha256_b64": base64.b64encode(b"\x00" * 32).decode()}, "checksum differs"),
    ],
)
def test_refuses_a_header_that_disagrees_with_the_intent(
    overrides: dict[str, Any], message: str
) -> None:
    payload = png()
    job = make_job(payload)
    storage = FakeStorage(quarantined(job, payload, **overrides), payload)

    with pytest.raises(ScanRejected, match=message):
        worker(storage).process(job)
    assert storage.put_key is None


def test_refuses_keys_outside_the_tenant_scope() -> None:
    payload = png()
    job = make_job(payload)
    storage = FakeStorage(quarantined(job, payload), payload)

    intruder = VerifyCoverJob(
        **{**job.__dict__, "source_key": f"tenants/{uuid4()}/quarantine/cover/{PRODUCT}"}
    )
    with pytest.raises(ScanRejected, match="quarantine key is outside"):
        worker(storage).process(intruder)

    intruder = VerifyCoverJob(**{**job.__dict__, "ready_key": f"tenants/{SELLER}/covers/anything"})
    with pytest.raises(ScanRejected, match="ready key is outside"):
        worker(storage).process(intruder)
    assert storage.put_key is None


def test_refuses_buckets_that_are_not_the_configured_ones() -> None:
    payload = png()
    job = make_job(payload)
    storage = FakeStorage(quarantined(job, payload), payload)

    intruder = VerifyCoverJob(**{**job.__dict__, "ready_bucket": "somewhere-else"})
    with pytest.raises(ScanRejected, match="storage boundaries"):
        worker(storage).process(intruder)
    assert storage.put_key is None


def test_a_rejected_image_never_reaches_the_served_bucket() -> None:
    # SVG passa pelo cabecalho: o PUT assinado so prende o tipo declarado, e
    # nada impede o vendedor de declarar PNG. Quem descobre e o verificador.
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    job = make_job(svg)
    storage = FakeStorage(quarantined(job, svg), svg)

    with pytest.raises(ScanRejected, match="markup"):
        worker(storage).process(job)
    assert storage.put_key is None


def test_the_scanner_decides_before_the_promotion() -> None:
    payload = png()
    job = make_job(payload)
    storage = FakeStorage(quarantined(job, payload), payload)

    with pytest.raises(ScanRejected, match="scanner rejected"):
        worker(storage, RefusingScanner()).process(job)
    assert storage.put_key is None

    with pytest.raises(ScannerUnavailable):
        worker(storage, UnhealthyScanner()).process(job)
    assert storage.put_key is None


def test_a_failed_write_takes_the_promoted_cover_back_out() -> None:
    payload = png()
    job = make_job(payload)
    storage = FakeStorage(quarantined(job, payload), payload)
    cover_worker = worker(storage)
    result = cover_worker.process(job)

    cover_worker.discard_ready_cover(result)

    assert storage.deleted_versions == [("ready", job.ready_key, "ready-v1")]


def test_an_abandoned_attempt_can_be_swept_without_a_result() -> None:
    payload = png()
    job = make_job(payload)
    storage = FakeStorage(quarantined(job, payload), payload)

    worker(storage).discard_abandoned_product_image(
        seller_id=SELLER, product_id=PRODUCT, kind="cover"
    )

    assert storage.deleted_keys == [("quarantine", cover_quarantine_key(SELLER, PRODUCT))]


def test_a_quarantine_that_will_not_die_is_reported_as_retryable() -> None:
    payload = png()
    job = make_job(payload)
    storage = FakeStorage(quarantined(job, payload), payload)
    storage.fail_quarantine_delete = True
    cover_worker = worker(storage)
    result = cover_worker.process(job)

    with pytest.raises(CleanupRetryableError):
        cover_worker.cleanup_source_after_commit(result)
    with pytest.raises(CleanupRetryableError):
        cover_worker.discard_abandoned_product_image(
            seller_id=SELLER, product_id=PRODUCT, kind="cover"
        )


class SlowWorker:
    """Um verificador que segura o lugar até ser liberado."""

    def __init__(self, gate: threading.Event) -> None:
        self.gate = gate
        self.started = threading.Semaphore(0)
        self.peak = 0
        self._live = 0
        self._lock = threading.Lock()

    def process(self, _job: VerifyCoverJob) -> str:
        with self._lock:
            self._live += 1
            self.peak = max(self.peak, self._live)
        self.started.release()
        self.gate.wait(timeout=5)
        with self._lock:
            self._live -= 1
        return "done"


@pytest.mark.asyncio
async def test_the_inline_verification_never_takes_more_than_its_share() -> None:
    # Verificar uma capa e caro e acontece dentro da requisicao, ao contrario do
    # PDF e do video, que vao para um worker com lease. Sem teto, um vendedor
    # autenticado dispara N confirmacoes e ocupa o pool de threads do processo —
    # que divide maquina com outros servicos.
    gate = threading.Event()
    slow = SlowWorker(gate)
    job = make_job(png())
    running = [
        asyncio.create_task(cover_routes._verify_within_budget(slow, job))  # type: ignore[arg-type]
        for _ in range(6)
    ]
    try:
        for _ in range(cover_routes._CONCURRENT_COVER_VERIFICATIONS):
            assert await asyncio.to_thread(slow.started.acquire, True, 5)
        await asyncio.sleep(0.05)
        assert slow.peak == cover_routes._CONCURRENT_COVER_VERIFICATIONS
    finally:
        gate.set()
        results = await asyncio.gather(*running, return_exceptions=True)
    assert any(result == "done" for result in results)


@pytest.mark.asyncio
async def test_a_saturated_queue_refuses_with_a_deadline_instead_of_waiting_forever(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Uma fila que cresce sem limite e o proprio problema. Recusar com prazo
    # devolve a decisao a quem chamou.
    monkeypatch.setattr(cover_routes, "_COVER_QUEUE_WAIT_SECONDS", 0.05)
    gate = threading.Event()
    slow = SlowWorker(gate)
    job = make_job(png())
    holders = [
        asyncio.create_task(cover_routes._verify_within_budget(slow, job))  # type: ignore[arg-type]
        for _ in range(cover_routes._CONCURRENT_COVER_VERIFICATIONS)
    ]
    try:
        for _ in range(cover_routes._CONCURRENT_COVER_VERIFICATIONS):
            assert await asyncio.to_thread(slow.started.acquire, True, 5)
        with pytest.raises(HTTPException) as refusal:
            await cover_routes._verify_within_budget(slow, job)  # type: ignore[arg-type]
        assert refusal.value.status_code == 503
        assert refusal.value.detail == "cover_verification_busy"
        assert (refusal.value.headers or {})["retry-after"] == "5"
    finally:
        gate.set()
        await asyncio.gather(*holders, return_exceptions=True)
