"""Configurações da loja: marca, texto próprio, logo e a promessa do produto (#352).

Três coisas que o vendedor configura sobre a própria loja, num só lugar:

1. **Texto e cor** — nome, tagline, "sobre" e cor de destaque. Uma leitura e
   uma escrita, ambas presas a ``seller.settings.*`` e ao papel de dono ou
   admin, que a função do banco confere de novo (0052).
2. **Logo e banner** (``{kind}`` na rota, 0054) — o mesmo caminho da capa do
   produto (``cover_routes``): o intent reserva a chave, os bytes vão direto do
   navegador para a quarentena, e a confirmação baixa, verifica, reescreve,
   escaneia e promove **antes** de abrir qualquer transação, pelas mesmas
   razões daquela rota. Só muda quem é dono da imagem: a loja, sem produto.
3. **Promessa** — a frase da página de vendas, campo do produto. Segue a regra
   dos outros campos do produto: ``catalog.draft.update`` e versão esperada.

O que a rota pública faz com o logo (trocar chave por URL) está em
``public_routes.py``. Aqui a leitura das configurações faz o mesmo para a
tela do vendedor, com o presign de 60 s da capa.
"""

from __future__ import annotations

import base64
from typing import Any
from uuid import UUID

import anyio
from anyio import to_thread
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from .asset_routes import _bounded_presigned_expiry, _iso_datetime
from .catalog_routes import (
    _authenticate,
    _canonical_uuid,
    _model_from_body,
    _require_seller,
)
from .catalog_routes import (
    _domain_error as _catalog_domain_error,
)
from .image_verifier import COVER_MAX_BYTES, is_cover_media
from .internal_requests import AuthenticatedInternalRequest
from .storage_s3 import S3Storage, StorageError
from .storage_worker import (
    SELLER_IMAGE_KINDS,
    MalwareScanner,
    ProductCoverWorker,
    ScannerUnavailable,
    ScanRejected,
    VerifyCoverJob,
    image_quarantine_key,
    image_ready_key,
)

router = APIRouter(prefix="/v1/me/sellers")
_MAX_SAFE_INTEGER = (1 << 53) - 1

# Dois logos por processo, como as capas: o trabalho de verificar acontece
# dentro da requisição, e este processo divide a VPS com outros serviços.
_CONCURRENT_LOGO_VERIFICATIONS = 2
_LOGO_QUEUE_WAIT_SECONDS = 5.0
_logo_verification_slots = anyio.Semaphore(_CONCURRENT_LOGO_VERIFICATIONS)

_SETTINGS_READ = "seller.settings.read"
_SETTINGS_UPDATE = "seller.settings.update"
_ACCENT_COLOR = "^#[0-9a-f]{6}$"


class SellerSettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    display_name: str = Field(min_length=2, max_length=120)
    tagline: str | None = Field(default=None, min_length=2, max_length=160)
    about: str | None = Field(default=None, min_length=2, max_length=600)
    accent_color: str | None = Field(default=None, pattern=_ACCENT_COLOR)

    @field_validator("display_name", "tagline", "about")
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if value != value.strip() or _contains_control(value):
            raise ValueError("text must be trimmed and free of control characters")
        return value


class LogoUploadIntentBody(BaseModel):
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
            raise ValueError("unsupported logo media type")
        return value

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("invalid SHA-256")
        return value


class LogoMutationBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    idempotency_key: str

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        return _canonical_uuid(value, "invalid idempotency key")


class ProductHeadlineBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    idempotency_key: str
    expected_product_version: int = Field(ge=1, le=_MAX_SAFE_INTEGER)
    headline: str | None = Field(default=None, min_length=2, max_length=200)

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        return _canonical_uuid(value, "invalid idempotency key")

    @field_validator("headline")
    @classmethod
    def validate_headline(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if value != value.strip() or _contains_control(value):
            raise ValueError("headline must be trimmed and free of control characters")
        return value


# ------------------------------------------------------------- texto e cor


@router.get("/{seller_id}/settings")
async def read_seller_settings(request: Request, seller_id: UUID) -> dict[str, Any]:
    auth, internal = await _authenticate(request, _SETTINGS_READ)
    await _require_seller(internal, seller_id)
    try:
        async with auth.domain_session(internal.envelope) as session:
            value = await session.scalar(
                text("select app_private.read_seller_settings(:actor,:seller)"),
                {"actor": internal.envelope.identity.user_id, "seller": seller_id},
            )
    except DBAPIError as error:
        raise _domain_error(error) from error
    return _settings_response(_json_object(value), getattr(request.app.state, "storage", None))


@router.patch("/{seller_id}/settings")
async def update_seller_settings(request: Request, seller_id: UUID) -> dict[str, Any]:
    auth, internal = await _authenticate(request, _SETTINGS_UPDATE)
    await _require_seller(internal, seller_id)
    body = await _model_from_body(SellerSettingsBody, internal)
    try:
        async with auth.domain_session(internal.envelope) as session:
            value = await session.scalar(
                text(
                    "select app_private.update_seller_settings("
                    ":actor,:seller,:display_name,:tagline,:about,:accent_color,:request_id)"
                ),
                {
                    "actor": internal.envelope.identity.user_id,
                    "seller": seller_id,
                    "display_name": body.display_name,
                    "tagline": body.tagline,
                    "about": body.about,
                    "accent_color": body.accent_color,
                    "request_id": internal.envelope.request_id,
                },
            )
    except DBAPIError as error:
        raise _domain_error(error) from error
    return _settings_response(_json_object(value), getattr(request.app.state, "storage", None))


# ------------------------------------------------------------------ logo


@router.post(
    "/{seller_id}/settings/{kind}/upload-intents",
    response_model=None,
    status_code=status.HTTP_201_CREATED,
)
async def create_logo_upload_intent(request: Request, seller_id: UUID, kind: str) -> JSONResponse:
    auth, internal = await _authenticate(request, _SETTINGS_UPDATE)
    await _require_seller(internal, seller_id)
    await _require_kind(internal, kind)
    body = await _model_from_body(LogoUploadIntentBody, internal)
    storage = await _storage(request, internal)
    settings = request.app.state.settings
    try:
        async with auth.domain_session(internal.envelope) as session:
            value = await session.scalar(
                text(
                    "select app_private.create_seller_image_upload_intent("
                    ":actor,:seller,:kind,:media_type,:byte_size,:sha256,"
                    ":quarantine_bucket,:ready_bucket,"
                    ":idempotency_key,:body_sha256,:request_id)"
                ),
                {
                    "actor": internal.envelope.identity.user_id,
                    "seller": seller_id,
                    "kind": kind,
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
        signed_expires_at = _bounded_presigned_expiry(
            intent["expires_at"], signed.expires_in_seconds
        )
    except IntegrityError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="logo_conflict") from error
    except DBAPIError as error:
        raise _domain_error(error) from error
    except (StorageError, ValueError, KeyError, TypeError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="logo_storage_unavailable",
        ) from error
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "upload_id": str(intent["upload_id"]),
            "upload_url": signed.url,
            "required_headers": signed.headers,
            "required_content_length": signed.required_content_length,
            "expires_at": _iso_datetime(signed_expires_at),
        },
        headers={"cache-control": "no-store", "referrer-policy": "no-referrer"},
    )


@router.post("/{seller_id}/settings/{kind}/uploads/{upload_id}/confirm")
async def confirm_logo_upload(
    request: Request, seller_id: UUID, kind: str, upload_id: UUID
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, _SETTINGS_UPDATE)
    await _require_seller(internal, seller_id)
    await _require_kind(internal, kind)
    body = await _model_from_body(LogoMutationBody, internal)
    storage = await _storage(request, internal)
    worker = ProductCoverWorker(storage, await _scanner(request, internal))
    settings = request.app.state.settings
    quarantine_key = image_quarantine_key(seller_id, kind)

    try:
        observed = await to_thread.run_sync(
            lambda: storage.head(bucket=settings.storage_quarantine_bucket, key=quarantine_key)
        )
    except StorageError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="logo_upload_missing"
        ) from error

    job = VerifyCoverJob(
        seller_id=seller_id,
        product_id=None,
        upload_id=upload_id,
        source_bucket=settings.storage_quarantine_bucket,
        ready_bucket=settings.storage_ready_bucket,
        source_key=quarantine_key,
        ready_key=image_ready_key(seller_id, kind, upload_id),
        expected_size=observed.size,
        expected_sha256_hex=_checksum_hex(observed.checksum_sha256_b64),
        declared_content_type=observed.content_type,
        kind=kind,
    )
    try:
        verified = await _verify_within_budget(worker, job)
    except ScanRejected as rejection:
        await _close_upload(
            auth=auth,
            internal=internal,
            seller_id=seller_id,
            kind=kind,
            upload_id=upload_id,
            closure="rejected",
            failure_code="verification_failed",
        )
        await to_thread.run_sync(
            lambda: worker.discard_abandoned_image(seller_id=seller_id, kind=kind)
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="logo_rejected"
        ) from rejection
    except (ScannerUnavailable, StorageError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="logo_storage_unavailable"
        ) from error

    try:
        async with auth.domain_session(internal.envelope) as session:
            row = (
                await session.execute(
                    text(
                        "select previous_object_key from app_private.set_seller_image("
                        ":actor,:seller,:kind,:upload,:object_key,:storage_version_id,"
                        ":media_type,:sha256,:width,:height,:byte_size,"
                        ":idempotency_key,:body_sha256,:request_id)"
                    ),
                    {
                        "actor": internal.envelope.identity.user_id,
                        "seller": seller_id,
                        "kind": kind,
                        "upload": upload_id,
                        "object_key": job.ready_key,
                        "storage_version_id": verified.ready.version_id,
                        "media_type": verified.media_type,
                        "sha256": verified.sha256_hex,
                        "width": verified.width,
                        "height": verified.height,
                        "byte_size": verified.byte_size,
                        "idempotency_key": UUID(body.idempotency_key),
                        "body_sha256": internal.envelope.body_sha256,
                        "request_id": internal.envelope.request_id,
                    },
                )
            ).one()
    except Exception as error:
        # O logo já está no bucket servido, mas nada aponta para ele.
        await to_thread.run_sync(lambda: worker.discard_ready_cover(verified))
        if isinstance(error, DBAPIError):
            raise _domain_error(error) from error
        raise

    await to_thread.run_sync(lambda: worker.cleanup_source_after_commit(verified))
    if row.previous_object_key:
        await _discard_replaced(storage, str(row.previous_object_key))
    return {
        "kind": kind,
        "image": {
            "media_type": verified.media_type,
            "byte_size": verified.byte_size,
            "width": verified.width,
            "height": verified.height,
        },
    }


@router.post("/{seller_id}/settings/{kind}/clear")
async def clear_logo(request: Request, seller_id: UUID, kind: str) -> dict[str, Any]:
    auth, internal = await _authenticate(request, _SETTINGS_UPDATE)
    await _require_seller(internal, seller_id)
    await _require_kind(internal, kind)
    body = await _model_from_body(LogoMutationBody, internal)
    storage = await _storage(request, internal)
    try:
        async with auth.domain_session(internal.envelope) as session:
            row = (
                await session.execute(
                    text(
                        "select changed, previous_object_key from app_private.clear_seller_image("
                        ":actor,:seller,:kind,:idempotency_key,:body_sha256,:request_id)"
                    ),
                    {
                        "actor": internal.envelope.identity.user_id,
                        "seller": seller_id,
                        "kind": kind,
                        "idempotency_key": UUID(body.idempotency_key),
                        "body_sha256": internal.envelope.body_sha256,
                        "request_id": internal.envelope.request_id,
                    },
                )
            ).one()
    except DBAPIError as error:
        raise _domain_error(error) from error
    if row.changed and row.previous_object_key:
        await _discard_replaced(storage, str(row.previous_object_key))
    return {"changed": bool(row.changed)}


# -------------------------------------------------------------- promessa


@router.patch("/{seller_id}/products/{product_id}/headline")
async def update_product_headline(
    request: Request, seller_id: UUID, product_id: UUID
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "catalog.draft.update")
    await _require_seller(internal, seller_id)
    body = await _model_from_body(ProductHeadlineBody, internal)
    try:
        async with auth.domain_session(internal.envelope) as session:
            row = (
                await session.execute(
                    text(
                        "select product_version, changed from app_private.update_product_headline("
                        ":actor,:seller,:product,:headline,:expected_product_version,"
                        ":idempotency_key,:body_sha256,:request_id)"
                    ),
                    {
                        "actor": internal.envelope.identity.user_id,
                        "seller": seller_id,
                        "product": product_id,
                        "headline": body.headline,
                        "expected_product_version": body.expected_product_version,
                        "idempotency_key": UUID(body.idempotency_key),
                        "body_sha256": internal.envelope.body_sha256,
                        "request_id": internal.envelope.request_id,
                    },
                )
            ).one()
    except DBAPIError as error:
        raise _domain_error(error) from error
    return {
        "product_version": int(row.product_version),
        "changed": bool(row.changed),
        "headline": body.headline,
    }


# --------------------------------------------------------------- apoio


def _settings_response(settings: dict[str, Any], storage: S3Storage | None) -> dict[str, Any]:
    """A projeção do banco, com o logo trocado por um endereço temporário.

    Chave e versão de objeto nunca saem daqui. Sem armazenamento, ou com a
    assinatura falhando, `logo` vira null e a tela mostra as iniciais — a
    mesma degradação da capa no editor.
    """
    return {
        "id": str(settings["id"]),
        "slug": str(settings["slug"]),
        "display_name": str(settings["display_name"]),
        "status": str(settings["status"]),
        "tagline": settings.get("tagline"),
        "about": settings.get("about"),
        "accent_color": settings.get("accent_color"),
        "logo": _signed_image(settings.get("logo"), storage),
        "banner": _signed_image(settings.get("banner"), storage),
        "changed": settings.get("changed"),
    }


def _signed_image(image: Any, storage: S3Storage | None) -> dict[str, Any] | None:
    if not isinstance(image, dict) or storage is None:
        return None
    try:
        request = storage.presign_cover(
            key=str(image["object_key"]),
            version_id=str(image["storage_version_id"]),
            media_type=str(image["media_type"]),
        )
        return {
            "url": request.url,
            "media_type": str(image["media_type"]),
            "width": int(image["width"]),
            "height": int(image["height"]),
            "expires_in_seconds": request.expires_in_seconds,
        }
    except (StorageError, KeyError, TypeError, ValueError):
        return None


async def _require_kind(internal: AuthenticatedInternalRequest, kind: str) -> None:
    if kind not in SELLER_IMAGE_KINDS:
        await internal.consume_rejected_request()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown_image_kind")


async def _verify_within_budget(worker: ProductCoverWorker, job: VerifyCoverJob) -> Any:
    with anyio.move_on_after(_LOGO_QUEUE_WAIT_SECONDS):
        await _logo_verification_slots.acquire()
        try:
            return await to_thread.run_sync(lambda: worker.process(job))
        finally:
            _logo_verification_slots.release()
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="logo_verification_busy",
        headers={"retry-after": "5"},
    )


async def _close_upload(
    *,
    auth: Any,
    internal: AuthenticatedInternalRequest,
    seller_id: UUID,
    kind: str,
    upload_id: UUID,
    closure: str,
    failure_code: str,
) -> None:
    try:
        async with auth.domain_session(internal.envelope) as session:
            await session.scalar(
                text(
                    "select app_private.close_seller_image_upload("
                    ":actor,:seller,:upload,:kind,:status,:failure_code,:request_id)"
                ),
                {
                    "actor": internal.envelope.identity.user_id,
                    "seller": seller_id,
                    "upload": upload_id,
                    "kind": kind,
                    "status": closure,
                    "failure_code": failure_code,
                    "request_id": internal.envelope.request_id,
                },
            )
    except DBAPIError:
        # A recusa já foi decidida pelo verificador; não registrá-la não pode
        # virar um 5xx que sugere que o arquivo talvez tenha passado.
        return


async def _discard_replaced(storage: S3Storage, object_key: str) -> None:
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
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="logo_storage_unavailable"
        )
    return storage


async def _scanner(request: Request, internal: AuthenticatedInternalRequest) -> MalwareScanner:
    scanner: MalwareScanner | None = getattr(request.app.state, "asset_scanner", None)
    if scanner is None:
        await internal.consume_rejected_request()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="logo_storage_unavailable"
        )
    return scanner


def _domain_error(error: DBAPIError) -> HTTPException:
    """O mapa do catálogo, com um código a mais.

    `55000` no catálogo é "publicação não pronta"; nas funções da 0052 é a loja
    fora de `active`. Mesmo SQLSTATE, outra frase — e a frase é o que o BFF
    mostra ao vendedor.
    """
    if getattr(error.orig, "sqlstate", None) == "55000":
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="seller_inactive")
    return _catalog_domain_error(error)


def _json_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="invalid_settings_response"
        )
    return value


def _checksum_hex(value: str) -> str:
    decoded = base64.b64decode(value, validate=True)
    if len(decoded) != 32:
        raise ValueError("invalid storage checksum")
    return decoded.hex()


def _contains_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)
