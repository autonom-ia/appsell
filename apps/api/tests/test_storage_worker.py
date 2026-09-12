from __future__ import annotations

import base64
import hashlib
import io
import struct
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from reportlab.pdfgen.canvas import Canvas  # type: ignore[import-untyped]

from appsell_api.storage_s3 import StorageCapabilityError, StoredObject
from appsell_api.storage_worker import (
    ClamAvScanner,
    CleanupRetryableError,
    DeterministicLocalScanner,
    PdfStorageWorker,
    ScannerUnavailable,
    ScanRejected,
    VerifyPdfJob,
    assert_storage_worker_ready,
)
from appsell_api.video_verifier import PreparedCatalogueVideo


def local_worker(storage: Any, scanner: Any) -> PdfStorageWorker:
    return PdfStorageWorker(storage, scanner, isolated_service_boundary=True)


def test_worker_normalizes_to_distinct_db_key_and_preserves_source_until_commit() -> None:
    payload = valid_pdf()
    job = make_job(payload)
    storage = FakeStorage(stored("quarantine", job.source_key, "source-v1", payload, job), payload)
    result = local_worker(storage, DeterministicLocalScanner()).process(job)
    assert storage.put_key == job.ready_key != job.source_key
    assert storage.ready_payload != payload
    assert result.source_sha256_hex == hashlib.sha256(payload).hexdigest()
    assert result.ready_sha256_hex == hashlib.sha256(storage.ready_payload).hexdigest()
    assert result.ready_sha256_hex != result.source_sha256_hex
    assert result.pdf_page_count == 1
    assert len(result.pdf_pages) == 1
    assert result.pdf_pages[0].stored.key.endswith("/v1/pages/1.png")
    assert storage.reconciled_prefixes == [("ready", result.ready.key.rsplit("/", 1)[0] + "/")]
    assert storage.deleted == []
    worker = local_worker(storage, DeterministicLocalScanner())
    worker.cleanup_source_after_commit(result)
    assert storage.deleted == [("quarantine", job.source_key, "source-v1")]


PREPARADO = b"MP4-COM-O-INDICE-NO-COMECO"


def _preparo_falso(events: list[str]) -> Any:
    """O sandbox, encenado: devolve um derivado diferente da origem."""

    @contextmanager
    def preparar(source: Path) -> Any:
        events.append("prepare")
        destino = source.parent / "preparado.mp4"
        destino.write_bytes(PREPARADO)
        try:
            yield PreparedCatalogueVideo(
                path=destino,
                byte_size=len(PREPARADO),
                sha256_hex=hashlib.sha256(PREPARADO).hexdigest(),
                attempts=1,
            )
        finally:
            destino.unlink(missing_ok=True)

    return preparar


def test_worker_promotes_the_prepared_video_and_scans_original_and_derivative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O que vai para o aluno é o derivado, e ele é escaneado como o do PDF.

    Antes de 31/08/2026 o vídeo do catálogo era guardado byte a byte como
    chegou, sem `+faststart`: o índice do MP4 ficava no fim e o aluno não
    conseguia arrastar a barra.
    """
    events: list[str] = []
    monkeypatch.setattr(
        "appsell_api.storage_worker.prepared_catalogue_video", _preparo_falso(events)
    )
    payload = valid_mp4()
    job = make_job(payload, media_type="video/mp4", file_name="aula.mp4")
    storage = FakeStorage(stored("quarantine", job.source_key, "source-v1", payload, job), payload)
    scanner = RecordingScanner(reject_call=99, events=events)
    result = local_worker(storage, scanner).process(job)
    assert events == ["scan", "prepare", "scan"]
    assert scanner.payloads == [payload, PREPARADO]
    assert storage.ready_payload == PREPARADO
    assert result.ready.content_type == "video/mp4"
    assert result.source_sha256_hex == hashlib.sha256(payload).hexdigest()
    assert result.ready_sha256_hex == hashlib.sha256(PREPARADO).hexdigest()
    assert result.source_byte_size == len(payload)
    assert result.ready_byte_size == len(PREPARADO)
    assert result.pdf_page_count == 0


def test_worker_never_promotes_a_prepared_video_the_scanner_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        "appsell_api.storage_worker.prepared_catalogue_video", _preparo_falso(events)
    )
    payload = valid_mp4()
    job = make_job(payload, media_type="video/mp4", file_name="aula.mp4")
    storage = FakeStorage(stored("quarantine", job.source_key, "source-v1", payload, job), payload)
    with pytest.raises(ScanRejected, match="prepared"):
        local_worker(storage, RecordingScanner(reject_call=2, events=events)).process(job)
    assert storage.put_key is None and storage.deleted == []


def test_worker_rejects_before_ready_put_on_hash_or_metadata_mismatch() -> None:
    payload = valid_pdf()
    job = make_job(payload)
    source = stored("quarantine", job.source_key, "source-v1", payload, job)
    source = StoredObject(**{**source.__dict__, "checksum_sha256_b64": "invalid"})
    storage = FakeStorage(source, payload)
    with pytest.raises(ScanRejected, match="checksum"):
        local_worker(storage, DeterministicLocalScanner()).process(job)
    assert storage.put_key is None and storage.deleted == []


def test_finalize_failure_compensates_ready_and_preserves_source() -> None:
    payload = valid_pdf()
    job = make_job(payload)
    storage = FakeStorage(stored("quarantine", job.source_key, "source-v1", payload, job), payload)
    worker = local_worker(storage, DeterministicLocalScanner())
    result = worker.process(job)
    worker.compensate_ready_after_finalize_failure(result)
    assert storage.deleted == [
        ("ready", result.pdf_pages[0].stored.key, "page-v1"),
        ("ready", job.ready_key, "ready-v1"),
    ]


def test_source_cleanup_failure_is_retryable_and_never_removes_ready() -> None:
    payload = valid_pdf()
    job = make_job(payload)
    storage = FakeStorage(stored("quarantine", job.source_key, "source-v1", payload, job), payload)
    worker = local_worker(storage, DeterministicLocalScanner())
    result = worker.process(job)
    storage.fail_source_delete = True
    with pytest.raises(CleanupRetryableError, match="retried"):
        worker.cleanup_source_after_commit(result)
    assert storage.deleted == []


def test_worker_scans_original_and_normalized_and_rejects_either_before_put() -> None:
    payload = valid_pdf()
    job = make_job(payload)
    source = stored("quarantine", job.source_key, "source-v1", payload, job)
    storage = FakeStorage(source, payload)
    scanner = RecordingScanner(reject_call=2)
    with pytest.raises(ScanRejected, match="normalized"):
        local_worker(storage, scanner).process(job)
    assert len(scanner.payloads) == 2
    assert scanner.payloads[0] == payload
    assert scanner.payloads[1] != payload
    assert storage.put_key is None


def test_original_scan_rejection_happens_before_pdf_parser() -> None:
    payload = b"%PDF-1.7\nmalicious-unparseable-fixture\n%%EOF\n"
    job = make_job(payload)
    source = stored("quarantine", job.source_key, "source-v1", payload, job)
    scanner = RecordingScanner(reject_call=1)
    with pytest.raises(ScanRejected, match="original"):
        local_worker(FakeStorage(source, payload), scanner).process(job)
    assert scanner.payloads == [payload]


def test_job_rejects_same_or_traversing_storage_keys() -> None:
    payload = valid_pdf()
    job = make_job(payload)
    with pytest.raises(ScanRejected, match="distinct"):
        local_worker(
            FakeStorage(stored("quarantine", job.source_key, "source-v1", payload, job), payload),
            DeterministicLocalScanner(),
        ).process(VerifyPdfJob(**{**job.__dict__, "ready_key": job.source_key}))
    with pytest.raises(ScanRejected, match="invalid"):
        local_worker(
            FakeStorage(stored("quarantine", job.source_key, "source-v1", payload, job), payload),
            DeterministicLocalScanner(),
        ).process(VerifyPdfJob(**{**job.__dict__, "source_key": "../escape"}))
    other_tenant_ready = f"tenants/{uuid4()}/assets/{uuid4()}/v1/{uuid4()}"
    with pytest.raises(ScanRejected, match="tenant scope"):
        local_worker(
            FakeStorage(stored("quarantine", job.source_key, "source-v1", payload, job), payload),
            DeterministicLocalScanner(),
        ).process(VerifyPdfJob(**{**job.__dict__, "ready_key": other_tenant_ready}))
    with pytest.raises(ScanRejected, match="buckets"):
        local_worker(
            FakeStorage(stored("quarantine", job.source_key, "source-v1", payload, job), payload),
            DeterministicLocalScanner(),
        ).process(VerifyPdfJob(**{**job.__dict__, "source_bucket": "wrong"}))


def test_remote_readiness_rejects_deterministic_scanner() -> None:
    storage: Any = SimpleNamespace(assert_runtime_controls=lambda **_kwargs: None)
    with pytest.raises(StorageCapabilityError, match="production-capable"):
        assert_storage_worker_ready(
            environment="production",
            storage=storage,
            scanner=DeterministicLocalScanner(),
            allowed_origin="https://sell.autonomia.site",
        )


class FakeStorage:
    quarantine_bucket = "quarantine"
    ready_bucket = "ready"

    def __init__(self, source: StoredObject, payload: bytes) -> None:
        self.source = source
        self.payload = payload
        self.ready_payload = b""
        self.page_payloads: dict[str, bytes] = {}
        self.put_key: str | None = None
        self.deleted: list[tuple[str, str, str]] = []
        self.reconciled_prefixes: list[tuple[str, str]] = []
        self.fail_source_delete = False

    def head(self, **_kwargs: Any) -> StoredObject:
        return self.source

    def open_download(self, **_kwargs: Any) -> Any:
        return io.BytesIO(self.payload)

    def put_verified(
        self,
        *,
        destination_bucket: str,
        destination_key: str,
        source_path: Path,
        byte_size: int,
        sha256_hex: str,
        content_type: str,
        upload_id: str,
        file_name: str,
    ) -> StoredObject:
        suffixes = {
            "application/pdf": ".pdf",
            "video/mp4": ".mp4",
            "image/png": ".png",
        }
        assert file_name.endswith(suffixes[content_type])
        payload = source_path.read_bytes()
        assert len(payload) == byte_size
        assert hashlib.sha256(payload).hexdigest() == sha256_hex
        if content_type == "image/png":
            self.page_payloads[destination_key] = payload
            version_id = "page-v1"
        else:
            self.put_key = destination_key
            self.ready_payload = payload
            version_id = "ready-v1"
        return StoredObject(
            bucket=destination_bucket,
            key=destination_key,
            version_id=version_id,
            size=byte_size,
            content_type=content_type,
            checksum_sha256_b64=base64.b64encode(bytes.fromhex(sha256_hex)).decode(),
            etag="ready-etag",
            appsell_upload_id=upload_id,
        )

    def delete_version(self, *, bucket: str, key: str, version_id: str) -> None:
        if bucket == self.quarantine_bucket and self.fail_source_delete:
            raise RuntimeError("injected source delete failure")
        self.deleted.append((bucket, key, version_id))

    def delete_all_versions_under_prefix(self, *, bucket: str, prefix: str) -> int:
        self.reconciled_prefixes.append((bucket, prefix))
        return 0


class RecordingScanner:
    provider_name = "recording-test"
    production_capable = False

    def __init__(self, *, reject_call: int, events: list[str] | None = None) -> None:
        self.reject_call = reject_call
        self.payloads: list[bytes] = []
        self.events = events

    def healthy(self) -> bool:
        return True

    def scan(self, path: Path) -> bool:
        if self.events is not None:
            self.events.append("scan")
        self.payloads.append(path.read_bytes())
        return len(self.payloads) != self.reject_call


@pytest.mark.parametrize(
    ("response", "expected"),
    [(b"stream: OK\0", True), (b"stream: Eicar-Test-Signature FOUND\0", False)],
)
def test_clamav_scanner_uses_bounded_unix_instream_protocol(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    response: bytes,
    expected: bool,
) -> None:
    sent: list[bytes] = []

    class FakeSocket:
        def __enter__(self) -> FakeSocket:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def settimeout(self, value: float) -> None:
            assert value == 3.0

        def connect(self, path: str) -> None:
            assert path == "/run/appsell-clamav/clamd.sock"

        def sendall(self, payload: bytes) -> None:
            sent.append(payload)

        def recv(self, _size: int) -> bytes:
            return response

    monkeypatch.setattr("appsell_api.storage_worker.socket.socket", lambda *_args: FakeSocket())
    source = tmp_path / "material.pdf"
    source.write_bytes(b"safe-pdf")

    assert ClamAvScanner("/run/appsell-clamav/clamd.sock").scan(source) is expected
    assert sent[0] == b"zINSTREAM\0"
    assert sent[-1] == b"\0\0\0\0"
    assert b"safe-pdf" in sent


def test_clamav_invalid_response_is_retryable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeSocket:
        def __enter__(self) -> FakeSocket:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def settimeout(self, _value: float) -> None:
            pass

        def connect(self, _path: str) -> None:
            pass

        def sendall(self, _payload: bytes) -> None:
            pass

        def recv(self, _size: int) -> bytes:
            return b"stream: ERROR\0"

    monkeypatch.setattr("appsell_api.storage_worker.socket.socket", lambda *_args: FakeSocket())
    source = tmp_path / "material.pdf"
    source.write_bytes(b"safe-pdf")

    with pytest.raises(ScannerUnavailable):
        ClamAvScanner("/run/appsell-clamav/clamd.sock").scan(source)


def test_assinatura_fresca_passa_mesmo_sem_o_mirror(monkeypatch: pytest.MonkeyPatch) -> None:
    """Era `test_clamav_health_requires_fresh_signatures`, e o contrato mudou.

    A versão anterior exigia assinatura com menos de 48h e mais nada. Isso
    reprovou, em 12/09/2026, uma base que era a mais recente publicada no
    mundo, e a loja ficou 3h06 fora do ar. A idade deixou de ser a medida
    principal; ela é a rede de segurança de quando o mirror não responde.

    O que este teste guarda é essa rede: assinatura fresca, sem conseguir falar
    com o mirror, continua aprovando.
    """
    scanner = _monta(monkeypatch, daily_local=28121, idade_horas=3, daily_publicada=None)

    assert scanner.healthy() is True


def make_job(
    payload: bytes,
    *,
    media_type: str = "application/pdf",
    file_name: str = "material.pdf",
) -> VerifyPdfJob:
    upload_id = uuid4()
    return VerifyPdfJob(
        seller_id=(seller_id := uuid4()),
        upload_id=upload_id,
        source_bucket="quarantine",
        ready_bucket="ready",
        source_key=f"tenants/{seller_id}/quarantine/{upload_id}",
        ready_key=f"tenants/{seller_id}/assets/{uuid4()}/v1/{uuid4()}",
        source_version_id="source-v1",
        expected_size=len(payload),
        expected_sha256_hex=hashlib.sha256(payload).hexdigest(),
        declared_content_type=media_type,
        file_name=file_name,
    )


def stored(
    bucket: str, key: str, version_id: str, payload: bytes, job: VerifyPdfJob
) -> StoredObject:
    return StoredObject(
        bucket=bucket,
        key=key,
        version_id=version_id,
        size=len(payload),
        content_type=job.declared_content_type,
        checksum_sha256_b64=base64.b64encode(hashlib.sha256(payload).digest()).decode(),
        etag="etag",
        appsell_upload_id=str(job.upload_id),
    )


def valid_pdf() -> bytes:
    output = io.BytesIO()
    canvas = Canvas(output, pagesize=(320, 480), invariant=1)
    canvas.drawString(32, 430, "Verified")
    canvas.showPage()
    canvas.save()
    return output.getvalue()


def valid_mp4() -> bytes:
    def mp4_box(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I4s", len(payload) + 8, kind) + payload

    return (
        mp4_box(b"ftyp", b"isom\0\0\0\0isommp42")
        + mp4_box(b"moov", b"metadata")
        + mp4_box(b"mdat", b"video")
    )


class _ClamdFalso:
    """Um `clamd` que responde `PONG` e depois a versão que o teste mandar.

    O iterador vem de fora porque `_request` abre um socket por comando: se
    cada abertura recomeçasse a sequência, o `VERSION` receberia `PONG` e o
    teste passaria a medir outra coisa.
    """

    def __init__(self, respostas: Iterator[bytes]) -> None:
        self.respostas = respostas

    def __enter__(self) -> _ClamdFalso:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def settimeout(self, _value: float) -> None:
        pass

    def connect(self, _path: str) -> None:
        pass

    def sendall(self, _payload: bytes) -> None:
        pass

    def recv(self, _size: int) -> bytes:
        return next(self.respostas)


def _monta(
    monkeypatch: pytest.MonkeyPatch,
    *,
    daily_local: int,
    idade_horas: float,
    daily_publicada: int | None,
) -> ClamAvScanner:
    construida = datetime.now(UTC) - timedelta(hours=idade_horas)
    versao = f"ClamAV 1.4.6/{daily_local}/{construida.strftime('%a %b %d %H:%M:%S %Y')}\0"
    respostas = iter((b"PONG\0", versao.encode()))
    monkeypatch.setattr(
        "appsell_api.storage_worker.socket.socket",
        lambda *_args: _ClamdFalso(respostas),
    )
    monkeypatch.setattr(
        "appsell_api.storage_worker.daily_publicada_no_mirror",
        lambda **_kwargs: daily_publicada,
    )
    return ClamAvScanner("/run/appsell-clamav/clamd.sock")


def test_assinatura_velha_mas_a_mais_recente_que_existe_e_saudavel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O caso que derrubou a loja em 12/09/2026.

    A assinatura tinha 50h e o limite era 48h, então `healthy()` reprovou. Só
    que a 28119 era a versão mais recente publicada no mundo naquele momento: o
    `clamd` estava de pé, com tudo o que existia para ter. Reprovar ali não
    protegia ninguém — não havia assinatura melhor para buscar — e custou 3h06
    de loja fora do ar.

    Idade é o calendário de publicação do fornecedor. Estar na versão mais
    recente é o que diz se estamos protegidos.
    """
    scanner = _monta(monkeypatch, daily_local=28119, idade_horas=50, daily_publicada=28119)

    assert scanner.healthy() is True


def test_assinatura_nova_mas_atrasada_do_mirror_ha_muito_tempo_reprova(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O caso que a regra antiga aprovava e não devia.

    Assinatura de 20h passa folgada em qualquer limite de idade. Mas se o
    mirror já publicou versões adiante e nós seguimos parados, o `freshclam`
    não está conseguindo atualizar — rede, disco, credencial, o que for. Aí
    estamos cegos de verdade, existe o que buscar, e há o que consertar.

    É esta a situação em que recusar o upload protege alguém.
    """
    scanner = _monta(monkeypatch, daily_local=28119, idade_horas=20, daily_publicada=28130)

    assert scanner.healthy() is False


def test_atraso_recente_do_mirror_nao_reprova(monkeypatch: pytest.MonkeyPatch) -> None:
    """Estar uma versão atrás por minutos é o `freshclam` fazendo o trabalho.

    Com verificação de hora em hora, existe uma janela normal entre o mirror
    publicar e nós baixarmos. Reprovar nela transformaria operação rotineira em
    incidente, que é o erro que este arquivo inteiro existe para não repetir.
    """
    scanner = _monta(monkeypatch, daily_local=28120, idade_horas=2, daily_publicada=28121)

    assert scanner.healthy() is True


def test_sem_resposta_do_mirror_cai_para_a_idade_com_limite_generoso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DNS fora não pode, sozinho, recusar upload.

    Sem resposta do mirror não dá para saber se estamos atrasados, e tratar
    isso como "cego" faria uma falha de DNS derrubar o envio de arquivo. A
    idade volta a valer como rede de segurança, com limite largo o bastante
    para os gaps normais do fornecedor.
    """
    quatro_dias = _monta(monkeypatch, daily_local=28119, idade_horas=96, daily_publicada=None)
    assert quatro_dias.healthy() is True

    trinta_dias = _monta(monkeypatch, daily_local=28119, idade_horas=720, daily_publicada=None)
    assert trinta_dias.healthy() is False


def test_clamd_fora_reprova_antes_de_qualquer_pergunta_sobre_versao(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sem `PONG` não há o que discutir, e o mirror nem chega a ser consultado."""
    consultas: list[int] = []

    def _nao_deveria(**_kwargs: object) -> int | None:
        consultas.append(1)
        return 28121

    monkeypatch.setattr("appsell_api.storage_worker.daily_publicada_no_mirror", _nao_deveria)
    monkeypatch.setattr(
        "appsell_api.storage_worker.socket.socket",
        lambda *_args: (_ for _ in ()).throw(OSError("sem socket")),
    )

    assert ClamAvScanner("/run/appsell-clamav/clamd.sock").healthy() is False
    assert consultas == [], "não faz sentido perguntar ao mirror com o scanner fora"


def test_mirror_anunciando_versao_menor_que_a_local_nao_aprova_sozinho(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Estar à frente do mirror não existe, então a resposta não serve.

    Não há como ter uma base que o mirror nunca publicou. Quando a conta dá
    negativa, ou o fornecedor reverteu uma publicação, ou a resposta não veio
    de quem devia — e a subtração crua aprovaria os dois casos: com um TXT
    forjado anunciando `1`, `1 - 28121` passa em qualquer teto e o antivírus
    seria dado como saudável para sempre, inclusive com o `freshclam` parado
    há meses.

    A resposta implausível é descartada e a idade decide, que é o mesmo
    caminho de quando o mirror não responde.
    """
    recente = _monta(monkeypatch, daily_local=28121, idade_horas=3, daily_publicada=1)
    assert recente.healthy() is True, "a idade ainda é boa, então o upload segue"

    parada = _monta(monkeypatch, daily_local=28121, idade_horas=720, daily_publicada=1)
    assert parada.healthy() is False, "sem mirror confiável, a idade precisa valer"
