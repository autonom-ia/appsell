"""Envio, troca e remoção da capa de um produto.

A capa não cabe no caminho normal. O envelope interno assinado lê no máximo
64 KiB, e uma capa vale até 5 MiB, então os bytes nunca passam por esta API:
o navegador manda direto para a quarentena com um PUT assinado, e o que chega
aqui depois é só o aviso de que o objeto existe.

Por causa disso a confirmação faz o trabalho pesado **antes** de abrir qualquer
transação: baixa, verifica, reescreve sem metadados, escaneia e promove. Só
então grava. Uma transação aberta durante um download de 5 MiB transformaria um
S3 lento em indisponibilidade de banco, e a alternativa — duas transações —
exigiria alargar a lista de continuações de capacidade, que é justamente o que
impede uma rota de trocar de autoridade no meio do caminho.

O preço é fazer trabalho à toa quando o pedido já não vale: a gravação é a
última etapa e pode recusar. Quando recusa, a capa promovida é apagada na volta.
"""

from __future__ import annotations

import base64
from typing import Any
from uuid import UUID

from anyio import to_thread
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from .catalog_routes import (
    _authenticate,
    _canonical_uuid,
    _domain_error,
    _model_from_body,
    _require_seller,
)
from .image_verifier import COVER_MAX_BYTES, is_cover_media
from .internal_requests import AuthenticatedInternalRequest
from .storage_s3 import S3Storage, StorageError
from .storage_worker import (
    MalwareScanner,
    ProductCoverWorker,
    ScannerUnavailable,
    ScanRejected,
    VerifyCoverJob,
    cover_quarantine_key,
    cover_ready_key,
)

router = APIRouter(prefix="/v1")
_MAX_SAFE_INTEGER = (1 << 53) - 1


class CoverUploadIntentBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    idempotency_key: str
    media_type: str
    byte_size: int = Field(ge=1, le=COVER_MAX_BYTES)
    sha256: str

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        return _canonical_uuid(value, "invalid idempotency key")

    @field_validator("media_type")
    @classmethod
    def validate_media_type(cls, value: str) -> str:
        if not is_cover_media(value):
            raise ValueError("unsupported cover media type")
        return value

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("invalid SHA-256")
        return value


class CoverMutationBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    idempotency_key: str
    expected_product_version: int = Field(ge=1, le=_MAX_SAFE_INTEGER)

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        return _canonical_uuid(value, "invalid idempotency key")


@router.post(
    "/me/sellers/{seller_id}/products/{product_id}/cover/upload-intents",
    response_model=None,
    status_code=status.HTTP_201_CREATED,
)
async def create_cover_upload_intent(
    request: Request, seller_id: UUID, product_id: UUID
) -> JSONResponse:
    auth, internal = await _authenticate(request, "catalog.draft.update")
    await _require_seller(internal, seller_id)
    body = await _model_from_body(CoverUploadIntentBody, internal)
    storage = await _storage(request, internal)
    settings = request.app.state.settings
    try:
        async with auth.domain_session(internal.envelope) as session:
            value = await session.scalar(
                text(
                    "select app_private.create_product_cover_upload_intent("
                    ":actor,:seller,:product,:media_type,:byte_size,:sha256,"
                    ":quarantine_bucket,:ready_bucket,"
                    ":idempotency_key,:body_sha256,:request_id)"
                ),
                {
                    "actor": internal.envelope.identity.user_id,
                    "seller": seller_id,
                    "product": product_id,
                    "media_type": body.media_type,
                    "byte_size": body.byte_size,
                    "sha256": body.sha256,
                    "quarantine_bucket": settings.storage_quarantine_bucket,
                    "ready_bucket": settings.storage_ready_bucket,
                    "idempotency_key": UUID(body.idempotency_key),
                    "body_sha256": internal.envelope.body_sha256,
                    "request_id": internal.envelope.request_id,
                },
            )
        intent = _json_object(value)
        signed = await to_thread.run_sync(
            lambda: storage.presign_cover_put(
                key=str(intent["quarantine_key"]),
                content_type=str(intent["media_type"]),
                sha256_hex=str(intent["sha256"]),
                byte_size=int(intent["byte_size"]),
                upload_request_id=str(intent["upload_id"]),
            )
        )
    except IntegrityError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="cover_conflict"
        ) from error
    except DBAPIError as error:
        raise _domain_error(error) from error
    except (StorageError, ValueError, KeyError, TypeError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="cover_storage_unavailable",
        ) from error
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "upload_id": str(intent["upload_id"]),
            "upload_url": signed.url,
            "required_headers": signed.headers,
            "required_content_length": signed.required_content_length,
            "expires_in_seconds": signed.expires_in_seconds,
            "product_version": int(intent["product_version"]),
        },
        headers={"cache-control": "no-store", "referrer-policy": "no-referrer"},
    )


@router.post("/me/sellers/{seller_id}/products/{product_id}/cover/uploads/{upload_id}/confirm")
async def confirm_cover_upload(
    request: Request, seller_id: UUID, product_id: UUID, upload_id: UUID
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "catalog.draft.update")
    await _require_seller(internal, seller_id)
    body = await _model_from_body(CoverMutationBody, internal)
    storage = await _storage(request, internal)
    worker = ProductCoverWorker(storage, await _scanner(request, internal))
    settings = request.app.state.settings
    quarantine_key = cover_quarantine_key(seller_id, product_id)

    try:
        observed = await to_thread.run_sync(
            lambda: storage.head(bucket=settings.storage_quarantine_bucket, key=quarantine_key)
        )
    except StorageError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="cover_upload_missing"
        ) from error

    job = VerifyCoverJob(
        seller_id=seller_id,
        product_id=product_id,
        upload_id=upload_id,
        source_bucket=settings.storage_quarantine_bucket,
        ready_bucket=settings.storage_ready_bucket,
        source_key=quarantine_key,
        ready_key=cover_ready_key(seller_id, product_id, upload_id),
        expected_size=observed.size,
        expected_sha256_hex=_checksum_hex(observed.checksum_sha256_b64),
        declared_content_type=observed.content_type,
    )
    try:
        verified = await to_thread.run_sync(lambda: worker.process(job))
    except ScanRejected as rejection:
        await _close_upload(
            auth=auth,
            internal=internal,
            seller_id=seller_id,
            product_id=product_id,
            upload_id=upload_id,
            closure="rejected",
            failure_code="verification_failed",
        )
        await to_thread.run_sync(
            lambda: worker.discard_abandoned_cover(seller_id=seller_id, product_id=product_id)
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="cover_rejected"
        ) from rejection
    except (ScannerUnavailable, StorageError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="cover_storage_unavailable"
        ) from error

    try:
        async with auth.domain_session(internal.envelope) as session:
            row = (
                await session.execute(
                    text(
                        "select product_version, previous_object_key"
                        " from app_private.set_product_cover("
                        ":actor,:seller,:product,:upload,:object_key,:storage_version_id,"
                        ":media_type,:sha256,:width,:height,:byte_size,"
                        ":expected_product_version,"
                        ":idempotency_key,:body_sha256,:request_id)"
                    ),
                    {
                        "actor": internal.envelope.identity.user_id,
                        "seller": seller_id,
                        "product": product_id,
                        "upload": upload_id,
                        "object_key": job.ready_key,
                        "storage_version_id": verified.ready.version_id,
                        "media_type": verified.media_type,
                        "sha256": verified.sha256_hex,
                        "width": verified.width,
                        "height": verified.height,
                        "byte_size": verified.byte_size,
                        "expected_product_version": body.expected_product_version,
                        "idempotency_key": UUID(body.idempotency_key),
                        "body_sha256": internal.envelope.body_sha256,
                        "request_id": internal.envelope.request_id,
                    },
                )
            ).one()
    except Exception as error:
        # A capa já está no bucket servido, mas nada aponta para ela. Apagar é
        # a única saída correta: deixá-la ali é pagar armazenamento por um
        # objeto que ninguém alcança.
        await to_thread.run_sync(lambda: worker.discard_ready_cover(verified))
        if isinstance(error, DBAPIError):
            raise _domain_error(error) from error
        raise

    await to_thread.run_sync(lambda: worker.cleanup_source_after_commit(verified))
    if row.previous_object_key:
        await _discard_replaced_cover(storage, str(row.previous_object_key))
    return {
        "product_version": int(row.product_version),
        "cover": {
            "media_type": verified.media_type,
            "byte_size": verified.byte_size,
            "width": verified.width,
            "height": verified.height,
        },
    }


@router.post("/me/sellers/{seller_id}/products/{product_id}/cover/clear")
async def clear_cover(request: Request, seller_id: UUID, product_id: UUID) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "catalog.draft.update")
    await _require_seller(internal, seller_id)
    body = await _model_from_body(CoverMutationBody, internal)
    storage = await _storage(request, internal)
    try:
        async with auth.domain_session(internal.envelope) as session:
            row = (
                await session.execute(
                    text(
                        "select product_version, changed, previous_object_key"
                        " from app_private.clear_product_cover("
                        ":actor,:seller,:product,:expected_product_version,"
                        ":idempotency_key,:body_sha256,:request_id)"
                    ),
                    {
                        "actor": internal.envelope.identity.user_id,
                        "seller": seller_id,
                        "product": product_id,
                        "expected_product_version": body.expected_product_version,
                        "idempotency_key": UUID(body.idempotency_key),
                        "body_sha256": internal.envelope.body_sha256,
                        "request_id": internal.envelope.request_id,
                    },
                )
            ).one()
    except DBAPIError as error:
        raise _domain_error(error) from error
    if row.changed and row.previous_object_key:
        await _discard_replaced_cover(storage, str(row.previous_object_key))
    return {"product_version": int(row.product_version), "changed": bool(row.changed)}


async def _close_upload(
    *,
    auth: Any,
    internal: AuthenticatedInternalRequest,
    seller_id: UUID,
    product_id: UUID,
    upload_id: UUID,
    closure: str,
    failure_code: str,
) -> None:
    try:
        async with auth.domain_session(internal.envelope) as session:
            await session.scalar(
                text(
                    "select app_private.close_product_cover_upload("
                    ":actor,:seller,:product,:upload,:status,:failure_code,:request_id)"
                ),
                {
                    "actor": internal.envelope.identity.user_id,
                    "seller": seller_id,
                    "product": product_id,
                    "upload": upload_id,
                    "status": closure,
                    "failure_code": failure_code,
                    "request_id": internal.envelope.request_id,
                },
            )
    except DBAPIError:
        # A recusa já foi decidida pelo verificador. Não conseguir registrá-la
        # não pode virar um 5xx que sugere que o arquivo talvez tenha passado.
        return


async def _discard_replaced_cover(storage: S3Storage, object_key: str) -> None:
    """Apaga a capa que acabou de sair do ar.

    Falhar aqui não desfaz nada: a capa nova já está gravada e é a que aparece.
    O que fica é um objeto órfão, e derrubar a resposta por causa dele faria o
    vendedor achar que a troca não aconteceu.
    """
    try:
        await to_thread.run_sync(
            lambda: storage.delete_all_versions(bucket=storage.ready_bucket, key=object_key)
        )
    except Exception:
        return


async def _storage(request: Request, internal: AuthenticatedInternalRequest) -> S3Storage:
    storage: S3Storage | None = getattr(request.app.state, "storage", None)
    if storage is None:
        await internal.consume_rejected_request()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="cover_storage_unavailable"
        )
    return storage


async def _scanner(request: Request, internal: AuthenticatedInternalRequest) -> MalwareScanner:
    scanner: MalwareScanner | None = getattr(request.app.state, "asset_scanner", None)
    if scanner is None:
        await internal.consume_rejected_request()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="cover_storage_unavailable"
        )
    return scanner


def _json_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="invalid_cover_response"
        )
    return value


def _checksum_hex(value: str) -> str:
    decoded = base64.b64decode(value, validate=True)
    if len(decoded) != 32:
        raise ValueError("invalid storage checksum")
    return decoded.hex()
