from __future__ import annotations

import hashlib
import io
import os
from uuid import uuid4

import httpx
import pytest
from reportlab.pdfgen.canvas import Canvas  # type: ignore[import-untyped]

from appsell_api.asset_media import max_bytes as asset_max_bytes
from appsell_api.config import Settings
from appsell_api.pdf_verifier import verified_pdf_file as _verified_pdf_file
from appsell_api.storage_s3 import (
    MULTIPART_PART_BYTES,
    S3Storage,
    StorageError,
    multipart_plan,
    object_sha256_matches,
)

ENDPOINT = os.environ.get("APPSELL_TEST_S3_ENDPOINT")
ACCESS_KEY = os.environ.get("APPSELL_TEST_S3_ACCESS_KEY")
SECRET_KEY = os.environ.get("APPSELL_TEST_S3_SECRET_KEY")

pytestmark = pytest.mark.skipif(
    not all((ENDPOINT, ACCESS_KEY, SECRET_KEY)),
    reason="real authenticated S3 endpoint is required",
)


def verified_pdf_file(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
    kwargs["isolated_service_boundary"] = True
    return _verified_pdf_file(*args, **kwargs)  # type: ignore[arg-type]


def test_real_s3_contract_version_checksum_cors_anonymous_and_copy() -> None:
    storage = storage_client()
    storage.ensure_local_ci_controls(allowed_origin="http://localhost:3000")
    # This test process runs outside the storage container, so this call proves
    # the actual host/browser-facing endpoint rather than the Docker DNS path.
    storage.assert_public_edge_controls(require_tls=False)
    payload = valid_pdf()
    digest = hashlib.sha256(payload).hexdigest()
    key = f"contract/{uuid4()}/asset.pdf"
    upload = storage.presign_put(
        bucket=storage.quarantine_bucket,
        key=key,
        content_type="application/pdf",
        sha256_hex=digest,
        byte_size=len(payload),
        upload_request_id=str(uuid4()),
    )
    allowed_preflight = httpx.options(
        upload.url,
        headers={
            "origin": "http://localhost:3000",
            "access-control-request-method": "PUT",
            "access-control-request-headers": (
                "content-type,x-amz-checksum-sha256,x-amz-meta-appsell-upload-id"
            ),
        },
        timeout=10,
    )
    assert allowed_preflight.status_code in {200, 204}
    assert allowed_preflight.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert allowed_preflight.headers.get("access-control-allow-credentials") != "true"
    response = httpx.put(upload.url, content=payload, headers=upload.headers, timeout=10)
    assert response.status_code in {200, 201}, response.text

    source = storage.head(bucket=storage.quarantine_bucket, key=key)
    assert source.size == len(payload)
    assert source.content_type == "application/pdf"
    assert object_sha256_matches(source, digest)
    assert source.appsell_upload_id != ""

    download = storage.presign_get(
        bucket=source.bucket,
        key=source.key,
        version_id=source.version_id,
        file_name="material.pdf",
    )
    fetched = httpx.get(download.url, timeout=10)
    assert fetched.status_code == 200
    assert fetched.content == payload
    assert fetched.headers.get("content-disposition") == ("inline; filename*=UTF-8''material.pdf")
    assert fetched.headers.get("cache-control") == "private, no-store, max-age=0"

    with storage.open_download(
        bucket=source.bucket, key=source.key, version_id=source.version_id
    ) as stream:
        with verified_pdf_file(
            stream,
            expected_size=source.size,
            expected_sha256_hex=digest,
            declared_content_type=source.content_type,
        ) as pdf:
            copied = storage.put_verified(
                destination_bucket=storage.ready_bucket,
                destination_key=f"ready/{uuid4()}/asset.pdf",
                source_path=pdf.ready_path,
                byte_size=pdf.ready_byte_size,
                sha256_hex=pdf.ready_sha256_hex,
                content_type="application/pdf",
                upload_id=source.appsell_upload_id,
                file_name="material.pdf",
            )
            assert pdf.ready_path.read_bytes() != payload
            assert copied.size == pdf.ready_byte_size
            assert copied.checksum_sha256_b64 != source.checksum_sha256_b64
    assert copied.version_id != ""
    assert copied.content_type == source.content_type
    assert copied.appsell_upload_id == source.appsell_upload_id
    storage.delete_version(bucket=source.bucket, key=source.key, version_id=source.version_id)
    storage.delete_version(bucket=copied.bucket, key=copied.key, version_id=copied.version_id)


def test_real_presign_rejects_wrong_length_and_local_limit() -> None:
    storage = storage_client()
    payload = valid_pdf()
    upload = storage.presign_put(
        bucket=storage.quarantine_bucket,
        key=f"contract/{uuid4()}/wrong-length.pdf",
        content_type="application/pdf",
        sha256_hex=hashlib.sha256(payload).hexdigest(),
        byte_size=len(payload),
        upload_request_id=str(uuid4()),
    )
    wrong = httpx.put(upload.url, content=payload + b"x", headers=upload.headers, timeout=10)
    assert wrong.status_code in {400, 403}, wrong.text
    wrong_checksum_headers = {**upload.headers, "x-amz-checksum-sha256": "AAAA"}
    wrong_checksum = httpx.put(
        upload.url, content=payload, headers=wrong_checksum_headers, timeout=10
    )
    assert wrong_checksum.status_code in {400, 403}, wrong_checksum.text
    wrong_metadata_headers = {
        **upload.headers,
        "x-amz-meta-appsell-upload-id": str(uuid4()),
    }
    wrong_metadata = httpx.put(
        upload.url, content=payload, headers=wrong_metadata_headers, timeout=10
    )
    assert wrong_metadata.status_code in {400, 403}, wrong_metadata.text
    tampered_url = upload.url.replace("wrong-length.pdf", "different-object.pdf")
    tampered = httpx.put(tampered_url, content=payload, headers=upload.headers, timeout=10)
    assert tampered.status_code in {400, 403}, tampered.text
    tampered_signature_url = upload.url.replace("X-Amz-Signature=", "X-Amz-Signature=0", 1)
    tampered_signature = httpx.put(
        tampered_signature_url, content=payload, headers=upload.headers, timeout=10
    )
    assert tampered_signature.status_code in {400, 403}, tampered_signature.text
    foreign_preflight = httpx.options(
        upload.url,
        headers={
            "origin": "https://evil.autonomia.site",
            "access-control-request-method": "PUT",
            "access-control-request-headers": "content-type,x-amz-checksum-sha256",
        },
        timeout=10,
    )
    assert foreign_preflight.headers.get("access-control-allow-origin") is None
    assert not storage.object_exists(bucket=storage.quarantine_bucket, key=upload_key(upload.url))
    # O tamanho vem de `asset_media`, nao de um numero escrito aqui. Ele ja
    # esteve fixo em 10 MiB, e desde `19dc049` — "PDF de ate 100 MiB" — o teste
    # passou a mandar um valor **dentro** do permitido e nao provava mais nada.
    # Ninguem percebeu porque a certificacao de storage so roda quando o diff
    # toca em storage, e ela ficou `skipped` por varios releases.
    limite_do_pdf = asset_max_bytes("application/pdf")
    with pytest.raises(ValueError, match="outside"):
        storage.presign_put(
            bucket=storage.quarantine_bucket,
            key="contract/too-large.pdf",
            content_type="application/pdf",
            sha256_hex="0" * 64,
            byte_size=limite_do_pdf + 1,
            upload_request_id=str(uuid4()),
        )
    # E o limite exato precisa ser aceito, senao "rejeita acima" viraria
    # "rejeita perto", e o teste passaria com o limite errado.
    storage.presign_put(
        bucket=storage.quarantine_bucket,
        key="contract/at-the-limit.pdf",
        content_type="application/pdf",
        sha256_hex="0" * 64,
        byte_size=limite_do_pdf,
        upload_request_id=str(uuid4()),
    )


def storage_client() -> S3Storage:
    return S3Storage(
        Settings(
            environment="ci",
            storage_provider="s3",
            storage_internal_endpoint=str(ENDPOINT),
            storage_public_endpoint=str(ENDPOINT),
            storage_quarantine_bucket="appsell-quarantine",
            storage_ready_bucket="appsell-ready",
            storage_control_access_key=str(ACCESS_KEY),
            storage_control_secret_key=str(SECRET_KEY),
            storage_allowed_origin="http://localhost:3000",
            malware_scanner_provider="deterministic",
            pdf_sandbox_url="http://pdf-sandbox.internal:8081/normalize",
        )
    )


def upload_key(url: str) -> str:
    marker = "/appsell-quarantine/"
    assert marker in url
    return url.split(marker, 1)[1].split("?", 1)[0]


def valid_pdf() -> bytes:
    output = io.BytesIO()
    canvas = Canvas(output, pagesize=(320, 480), invariant=1)
    canvas.drawString(32, 430, "S3 contract")
    canvas.showPage()
    canvas.save()
    return output.getvalue()


def test_real_s3_multipart_round_trip_resume_and_abort() -> None:
    """O envio em partes, contra o storage de verdade, do início ao fim.

    O que este teste prova e nenhum outro prova: que o objeto montado a partir
    das partes chega com o tipo e o cabeçalho de identificação que o worker
    exige, que a listagem enxerga o que já subiu (é isso que permite retomar), e
    que cancelar apaga as partes em vez de deixá-las ocupando disco.
    """
    storage = storage_client()
    payload = b"\xa5" * (MULTIPART_PART_BYTES + 1024 * 1024)
    plano = multipart_plan(len(payload))
    assert plano.part_count == 2
    key = f"contract/{uuid4()}/aula.mp4"
    upload_request_id = str(uuid4())

    storage_upload_id = storage.create_multipart_upload(
        bucket=storage.quarantine_bucket,
        key=key,
        content_type="video/mp4",
        byte_size=len(payload),
        upload_request_id=upload_request_id,
    )

    enviadas = []
    inicio = 0
    for numero in range(1, plano.part_count + 1):
        tamanho = plano.bytes_for_part(numero)
        pedaco = payload[inicio : inicio + tamanho]
        inicio += tamanho
        assinada = storage.presign_upload_part(
            bucket=storage.quarantine_bucket,
            key=key,
            storage_upload_id=storage_upload_id,
            part_number=numero,
            plan=plano,
            sha256_hex=hashlib.sha256(pedaco).hexdigest(),
        )
        resposta = httpx.put(assinada.url, content=pedaco, headers=assinada.headers, timeout=60)
        assert resposta.status_code == 200, resposta.text
        enviadas.append(numero)
        # A listagem é o que permite retomar: sem ela o navegador teria de
        # recomeçar do zero depois de qualquer interrupção.
        parciais = storage.list_multipart_parts(
            bucket=storage.quarantine_bucket, key=key, storage_upload_id=storage_upload_id
        )
        assert [parte.part_number for parte in parciais] == enviadas

    partes = storage.list_multipart_parts(
        bucket=storage.quarantine_bucket, key=key, storage_upload_id=storage_upload_id
    )
    storage.complete_multipart_upload(
        bucket=storage.quarantine_bucket,
        key=key,
        storage_upload_id=storage_upload_id,
        plan=plano,
        parts=partes,
    )

    montado = storage.head(bucket=storage.quarantine_bucket, key=key)
    assert montado.size == len(payload)
    assert montado.content_type == "video/mp4"
    # O worker recusa o objeto se este cabeçalho não bater com a intenção, e no
    # envio em partes ele é gravado na abertura, não nas partes.
    assert montado.appsell_upload_id == upload_request_id

    abandonado = f"contract/{uuid4()}/abandonada.mp4"
    outro = storage.create_multipart_upload(
        bucket=storage.quarantine_bucket,
        key=abandonado,
        content_type="video/mp4",
        byte_size=len(payload),
        upload_request_id=str(uuid4()),
    )
    primeira = storage.presign_upload_part(
        bucket=storage.quarantine_bucket,
        key=abandonado,
        storage_upload_id=outro,
        part_number=1,
        plan=plano,
        sha256_hex=hashlib.sha256(payload[: plano.bytes_for_part(1)]).hexdigest(),
    )
    httpx.put(
        primeira.url,
        content=payload[: plano.bytes_for_part(1)],
        headers=primeira.headers,
        timeout=60,
    )
    storage.abort_multipart_upload(
        bucket=storage.quarantine_bucket, key=abandonado, storage_upload_id=outro
    )
    # Cancelar tem de apagar as partes. Sem isto elas ficam ocupando disco sem
    # aparecer em listagem de objeto nenhuma.
    with pytest.raises(StorageError):
        storage.list_multipart_parts(
            bucket=storage.quarantine_bucket, key=abandonado, storage_upload_id=outro
        )
