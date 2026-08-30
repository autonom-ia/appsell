from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
from collections import OrderedDict
from pathlib import Path
from typing import Annotated, Any, Final, Literal
from uuid import UUID

import structlog
from anyio import to_thread
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .asset_media import is_document
from .catalog_routes import _domain_error
from .config import Settings
from .internal_requests import AuthenticatedInternalRequest, InternalRequestAuthenticator
from .learning_pdf import (
    LearningPdfRejected,
    LearningPdfUnavailable,
    authorized_pdf_page,
    render_prepared_pdf_page,
)
from .pdf_link_video import (
    DERIVATIVE_MEDIA_TYPE,
    MAX_PENDING_PREPARATIONS,
    PdfLinkVideoRejected,
    PdfLinkVideoUnavailable,
    PreparationRegistry,
    derivative_key,
    origin_validator,
    prepare_video,
    temporary_workspace,
    trusted_video_target,
)
from .pdf_links import (
    PdfLinksRejected,
    PdfLinksUnavailable,
    read_remote_pdf_link_manifest,
)
from .pdf_raster import license_footer_dimensions
from .storage_s3 import S3Storage, StorageError

router = APIRouter(prefix="/v1")
_MAX_LEARNING_BODY_BYTES = 4_096
_ACCESS_TOKEN_DIGEST = re.compile(r"[0-9a-f]{64}")
_MAX_PDF_STUDY_BODY_BYTES = 65_536
_MAX_SAFE_INTEGER = (1 << 53) - 1
_LEARNING_ASSETS_CONTRACT_HEADER = "x-appsell-learning-assets-contract"
logger = structlog.get_logger(__name__)
_PDF_LINK_VIDEO_PREPARATIONS = PreparationRegistry()
#: Referência forte para as tarefas em andamento. Sem isto o coletor de lixo
#: pode recolher a tarefa no meio do preparo — o `asyncio` só guarda referência
#: fraca, e a documentação pede que quem cria mantenha a sua.
_PDF_LINK_VIDEO_TASKS: set[asyncio.Task[None]] = set()
#: Quanto da fila o adiantamento pode ocupar.
#:
#: Metade. Quem clicou num botão está esperando na frente da tela; quem foi
#: adiantado ainda nem chegou na página. A reserva garante que o primeiro nunca
#: perca a vez para o segundo.
_WARM_PREPARATION_BUDGET: Final = MAX_PENDING_PREPARATIONS // 2
#: Quantos preparos correm ao mesmo tempo.
#:
#: Medido no vídeo do vendedor: 1,3 s de download e 0,6 s de conversão. O que
#: domina é a rede, e ela é da API — a conversão acontece no serviço isolado,
#: que atende um por vez de propósito e se recicla depois de cada arquivo.
#:
#: Então três aqui deixam três downloads correrem juntos enquanto as conversões
#: se enfileiram no serviço, que é rápido. Subir mais não ajudaria: passaria a
#: esperar na fila do isolamento, que não se abre mãos.
_PDF_LINK_VIDEO_SLOTS = asyncio.Semaphore(3)
_PROTECTED_PDF_SLOTS = asyncio.Semaphore(4)
_PROTECTED_PDF_SLOT_TIMEOUT_SECONDS = 8.0
_PDF_LINK_CACHE_LOCK = asyncio.Lock()
_PDF_LINK_CACHE: OrderedDict[str, tuple[dict[str, Any], ...]] = OrderedDict()
_PDF_LINK_CACHE_SIZE = 32


class LessonProgressBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    idempotency_key: str
    progress_percent: int = Field(ge=0, le=100)
    expected_version: int = Field(ge=0, le=_MAX_SAFE_INTEGER)

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        return _canonical_uuid(value, "invalid idempotency key")


class LessonCompletionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    idempotency_key: str
    expected_version: int = Field(ge=0, le=_MAX_SAFE_INTEGER)

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        return _canonical_uuid(value, "invalid idempotency key")


class FreeAccessBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    idempotency_key: str
    seller_id: str
    product_id: str

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        return _canonical_uuid(value, "invalid idempotency key")

    @field_validator("seller_id")
    @classmethod
    def validate_seller_id(cls, value: str) -> str:
        return _canonical_uuid(value, "invalid seller id")

    @field_validator("product_id")
    @classmethod
    def validate_product_id(cls, value: str) -> str:
        return _canonical_uuid(value, "invalid product id")


class AccessLinkRedeemBody(BaseModel):
    """Redemption of a seat-limited invitation.

    Only the digest of the link token travels here. The BFF hashes the token it
    received in the URL, so the raw value never reaches this service, never
    reaches the database, and cannot surface in a log line or a stack trace.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    idempotency_key: str
    token_sha256: str

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        return _canonical_uuid(value, "invalid idempotency key")

    @field_validator("token_sha256")
    @classmethod
    def validate_token_digest(cls, value: str) -> str:
        if _ACCESS_TOKEN_DIGEST.fullmatch(value) is None:
            raise ValueError("invalid token digest")
        return value


class PdfStudyPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class PdfStudyAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    page: int = Field(ge=1, le=100)
    tool: Literal["pen", "highlight"]
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    width: float = Field(ge=0.0005, le=0.05)
    points: list[PdfStudyPoint] = Field(min_length=2, max_length=1024)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _canonical_uuid(value, "invalid annotation id")


class PdfStudyAnnotationUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["annotation.upsert"]
    annotation: PdfStudyAnnotation


class PdfStudyAnnotationDelete(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["annotation.delete"]
    id: str

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _canonical_uuid(value, "invalid annotation id")


class PdfStudyBookmarkSet(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["bookmark.set"]
    page: int = Field(ge=1, le=100)
    active: bool


PdfStudyMutation = Annotated[
    PdfStudyAnnotationUpsert | PdfStudyAnnotationDelete | PdfStudyBookmarkSet,
    Field(discriminator="action"),
]


class PdfStudyMutationBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    idempotency_key: str
    expected_revision: int = Field(ge=0, le=_MAX_SAFE_INTEGER)
    mutation: PdfStudyMutation

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        return _canonical_uuid(value, "invalid idempotency key")


async def _authenticate(
    request: Request, capability: str
) -> tuple[InternalRequestAuthenticator, AuthenticatedInternalRequest]:
    auth: InternalRequestAuthenticator | None = getattr(request.app.state, "internal_auth", None)
    if auth is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="internal_auth_not_configured",
        )
    return auth, await auth.authenticate(request, capability)


@router.get("/me/library")
async def read_library(request: Request) -> dict[str, Any]:
    return await _read_buyer_products(request)


@router.get("/me/products")
async def read_buyer_products(request: Request) -> dict[str, Any]:
    return await _read_buyer_products(request)


@router.post("/me/library/enroll")
async def enroll_public_product(request: Request) -> Response:
    auth, internal = await _authenticate(request, "library.enroll")
    await _require_buyer_scope(internal)
    body = await _model_from_internal_body(request, internal, FreeAccessBody)
    try:
        async with auth.domain_session(internal.envelope) as session:
            row = (
                (
                    await session.execute(
                        text(
                            "select entitlement_id,created "
                            "from app_private.grant_public_product_access("
                            ":identity_user_id,:seller_id,:product_id,:idempotency_key,"
                            ":body_sha256,:request_id)"
                        ),
                        {
                            "identity_user_id": internal.envelope.identity.user_id,
                            "seller_id": UUID(body.seller_id),
                            "product_id": UUID(body.product_id),
                            "idempotency_key": UUID(body.idempotency_key),
                            "body_sha256": internal.envelope.body_sha256,
                            "request_id": internal.envelope.request_id,
                        },
                    )
                )
                .mappings()
                .one()
            )
    except DBAPIError as error:
        raise _learning_domain_error(error) from error
    return Response(
        content=json.dumps(
            {
                "status": "active",
                "entitlement_id": str(row["entitlement_id"]),
                "created": bool(row["created"]),
            }
        ),
        status_code=status.HTTP_201_CREATED if row["created"] else status.HTTP_200_OK,
        media_type="application/json",
    )


@router.post("/me/library/access-links/redeem")
async def redeem_access_link(request: Request) -> Response:
    auth, internal = await _authenticate(request, "library.enroll")
    await _require_buyer_scope(internal)
    body = await _model_from_internal_body(request, internal, AccessLinkRedeemBody)
    try:
        async with auth.domain_session(internal.envelope) as session:
            row = (
                (
                    await session.execute(
                        text(
                            "select entitlement_id,seller_organization_id,catalog_product_id,"
                            "created,seat_consumed,seats_remaining "
                            "from app_private.redeem_product_access_link("
                            ":identity_user_id,:token_sha256,:idempotency_key,"
                            ":body_sha256,:request_id)"
                        ),
                        {
                            "identity_user_id": internal.envelope.identity.user_id,
                            "token_sha256": body.token_sha256,
                            "idempotency_key": UUID(body.idempotency_key),
                            "body_sha256": internal.envelope.body_sha256,
                            "request_id": internal.envelope.request_id,
                        },
                    )
                )
                .mappings()
                .one()
            )
    except DBAPIError as error:
        raise _learning_domain_error(error) from error
    return Response(
        content=json.dumps(
            {
                "status": "active",
                "entitlement_id": str(row["entitlement_id"]),
                "seller_id": str(row["seller_organization_id"]),
                "product_id": str(row["catalog_product_id"]),
                "created": bool(row["created"]),
                "seat_consumed": bool(row["seat_consumed"]),
                "seats_remaining": row["seats_remaining"],
            }
        ),
        status_code=(status.HTTP_201_CREATED if row["seat_consumed"] else status.HTTP_200_OK),
        media_type="application/json",
    )


async def _read_buyer_products(request: Request) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "library.read")
    await _require_buyer_scope(internal)
    try:
        async with auth.domain_session(internal.envelope) as session:
            products = await session.scalar(
                text("select app_private.read_buyer_products(:identity_user_id)"),
                {"identity_user_id": internal.envelope.identity.user_id},
            )
    except DBAPIError as error:
        raise _domain_error(error) from error
    return {"items": products or []}


@router.get("/me/products/{entitlement_id}/seller-products")
async def read_buyer_seller_products(request: Request, entitlement_id: UUID) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "library.read")
    await _require_buyer_scope(internal)
    try:
        async with auth.domain_session(internal.envelope) as session:
            products = await session.scalar(
                text(
                    "select app_private.read_buyer_seller_storefront("
                    ":identity_user_id,:entitlement_id)"
                ),
                {
                    "identity_user_id": internal.envelope.identity.user_id,
                    "entitlement_id": entitlement_id,
                },
            )
    except DBAPIError as error:
        raise _learning_domain_error(error) from error
    return {"items": products or []}


@router.get("/me/learning/items/{entitlement_id}/assets/{asset_id}")
async def authorize_asset(request: Request, entitlement_id: UUID, asset_id: UUID) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "library.asset.read")
    await _require_buyer_scope(internal)
    try:
        async with auth.domain_session(internal.envelope) as session:
            row = (
                (
                    await session.execute(
                        text(
                            "select asset_id,asset_version,file_name,title,media_type,byte_size,"
                            "sha256,storage_driver,storage_bucket,object_key,storage_version_id,"
                            "storage_etag from app_private.authorize_publication_asset("
                            ":identity_user_id,:entitlement_id,:asset_id)"
                        ),
                        {
                            "identity_user_id": internal.envelope.identity.user_id,
                            "entitlement_id": entitlement_id,
                            "asset_id": asset_id,
                        },
                    )
                )
                .mappings()
                .one()
            )
    except DBAPIError as error:
        raise _domain_error(error) from error
    # A document is downloaded, never opened in the page, so this route hands
    # back no inline locator for one. The download route is the only way in,
    # and it is gated on the seller having marked the material downloadable.
    if is_document(row["media_type"]):
        return {
            "asset": {
                "id": str(row["asset_id"]),
                "delivery": "download_only",
                "file_name": row["file_name"],
                "title": row["title"],
                "media_type": row["media_type"],
                "byte_size": row["byte_size"],
                "sha256": row["sha256"],
            }
        }
    if row["media_type"] == "application/pdf":
        return {
            "asset": {
                "id": str(row["asset_id"]),
                "delivery": "protected_pages",
                "file_name": row["file_name"],
                "title": row["title"],
                "media_type": row["media_type"],
                "byte_size": row["byte_size"],
                "sha256": row["sha256"],
            }
        }
    if row["storage_driver"] == "s3":
        storage: S3Storage | None = getattr(request.app.state, "storage", None)
        if storage is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="asset_storage_unavailable",
            )
        try:
            signed = await to_thread.run_sync(
                lambda: storage.presign_get(
                    bucket=str(row["storage_bucket"]),
                    key=str(row["object_key"]),
                    version_id=str(row["storage_version_id"]),
                    file_name=str(row["file_name"]),
                    media_type=str(row["media_type"]),
                    disposition="inline",
                )
            )
        except (StorageError, ValueError) as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="asset_storage_unavailable",
            ) from error
        return {
            "asset": {
                "id": str(row["asset_id"]),
                "delivery": "redirect",
                "download_url": signed.url,
                "expires_in_seconds": signed.expires_in_seconds,
                "file_name": row["file_name"],
                "title": row["title"],
                "media_type": row["media_type"],
                "byte_size": row["byte_size"],
                "sha256": row["sha256"],
            }
        }
    if row["storage_driver"] != "legacy_bundle":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="asset_storage_driver_unavailable",
        )
    return {
        "asset": {
            "id": str(row["asset_id"]),
            "delivery": "legacy_bundle",
            "seed_key": str(row["file_name"]).rsplit(".", 1)[0],
            "file_name": row["file_name"],
            "title": row["title"],
            "media_type": row["media_type"],
            "byte_size": row["byte_size"],
            "sha256": row["sha256"],
        }
    }


@router.get("/me/learning/items/{entitlement_id}/assets/{asset_id}/pages")
async def inspect_asset_pages(
    request: Request, entitlement_id: UUID, asset_id: UUID
) -> dict[str, Any]:
    try:
        _, manifest, source = await _authorized_pdf_manifest(request, entitlement_id, asset_id)
        links = await _protected_pdf_links(request, manifest, source)
    except DBAPIError as error:
        raise _learning_domain_error(error) from error
    # O aluno abriu o PDF. Os vídeos que ele cita começam a ser preparados
    # agora, enquanto ele lê — quando chegar na página do botão, o arquivo já
    # está pronto e ele não espera nada.
    #
    # É a mesma preparação do clique, só que antes. Quem já está pronto não
    # refaz nada, e quem falhar não atrapalha a leitura: a resposta desta rota
    # sai igual de qualquer jeito.
    _warm_pdf_link_videos(request, manifest, links)
    return {
        "asset": {
            "id": str(manifest["asset_id"]),
            "page_count": int(manifest["page_count"]),
            "links": links,
        }
    }


def _warm_pdf_link_videos(request: Request, manifest: Any, links: list[dict[str, Any]]) -> None:
    """Começa a preparar os vídeos do PDF, sem segurar a resposta."""
    storage: S3Storage | None = getattr(request.app.state, "storage", None)
    settings = request.app.state.settings
    if storage is None or not settings.storage_configured:
        return
    bucket = settings.storage_ready_bucket
    seller = str(manifest["seller_organization_id"])
    for link in links:
        if link.get("kind") != "video":
            continue
        if len(_PDF_LINK_VIDEO_TASKS) >= _WARM_PREPARATION_BUDGET:
            # O adiantamento fica com metade da fila, não com ela toda. Sem esta
            # reserva, abrir um PDF com oito vídeos encheria o teto e o próximo
            # aluno que clicasse num botão receberia o prazo longo de fila cheia
            # — mais lento do que antes de existir adiantamento.
            return
        target = trusted_video_target(link.get("target_url"))
        if target is None:
            continue
        key = derivative_key(seller, target)
        if _PDF_LINK_VIDEO_PREPARATIONS.rejected(key):
            continue
        if not _PDF_LINK_VIDEO_PREPARATIONS.start(key):
            continue
        task = asyncio.create_task(
            _run_pdf_link_video_preparation(storage, bucket, key, target, replace=False)
        )
        _PDF_LINK_VIDEO_TASKS.add(task)
        task.add_done_callback(_PDF_LINK_VIDEO_TASKS.discard)


@router.get("/me/learning/items/{entitlement_id}/assets/{asset_id}/pdf-links/{link_id}/video")
async def prepare_pdf_link_video(
    request: Request, entitlement_id: UUID, asset_id: UUID, link_id: str, response: Response
) -> dict[str, Any]:
    """O endereço de um vídeo do PDF que o navegador consegue tocar.

    A origem costuma ser QuickTime, que o Chrome recusa. Aqui o servidor
    entrega um MP4 equivalente — preparado uma vez e guardado no storage — em
    vez de repassar o arquivo original e deixar o aluno sem saída.

    Enquanto o preparo não terminou, responde `202` com o intervalo de nova
    tentativa. É o mesmo desenho do envio de material no painel do vendedor: o
    trabalho demorado não segura a requisição.
    """
    if not re.fullmatch(r"[0-9a-f]{16}", link_id):
        raise _learning_not_found()
    try:
        _, manifest, source = await _authorized_pdf_manifest(request, entitlement_id, asset_id)
        links = await _protected_pdf_links(request, manifest, source)
    except DBAPIError as error:
        raise _learning_domain_error(error) from error
    link = next((item for item in links if item["id"] == link_id and item["kind"] == "video"), None)
    if link is None:
        raise _learning_not_found()
    target = trusted_video_target(link["target_url"])
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="protected_video_unavailable",
        )

    storage: S3Storage | None = getattr(request.app.state, "storage", None)
    settings = request.app.state.settings
    if storage is None or not settings.storage_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="protected_video_unavailable",
        )
    bucket = settings.storage_ready_bucket
    key = derivative_key(str(manifest["seller_organization_id"]), target)

    ready = await to_thread.run_sync(lambda: _pdf_link_video_ready(storage, bucket, key))
    estado = await _pdf_link_video_state(ready, target)
    if estado == "gone":
        # A origem existe e disse que este arquivo não. Servir a cópia guardada
        # manteria no ar algo que foi retirado.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="protected_video_unavailable",
        )
    if estado == "current":
        return await _pdf_link_video_ready_response(storage, bucket, key, ready)

    stale = ready is not None
    if _PDF_LINK_VIDEO_PREPARATIONS.rejected(key):
        # A recusa impede trabalho novo, não a entrega do que já existe: durante
        # a janela o aluno continua vendo a versão anterior em vez de nada.
        if ready is not None:
            return await _pdf_link_video_ready_response(storage, bucket, key, ready)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="protected_video_unavailable",
        )
    if len(_PDF_LINK_VIDEO_TASKS) >= MAX_PENDING_PREPARATIONS:
        # A fila e recusada cedo, e nao acumulada: um PDF pode citar centenas de
        # enderecos, e cada um viraria uma tarefa viva na frente das outras.
        response.status_code = status.HTTP_202_ACCEPTED
        return {"video": {"status": "preparing", "retry_after_seconds": 15}}
    if _PDF_LINK_VIDEO_PREPARATIONS.start(key):
        task = asyncio.create_task(
            _run_pdf_link_video_preparation(storage, bucket, key, target, replace=stale)
        )
        _PDF_LINK_VIDEO_TASKS.add(task)
        task.add_done_callback(_PDF_LINK_VIDEO_TASKS.discard)
    response.status_code = status.HTTP_202_ACCEPTED
    return {"video": {"status": "preparing", "retry_after_seconds": 3}}


async def _pdf_link_video_ready_response(
    storage: S3Storage, bucket: str, key: str, ready: Any
) -> dict[str, Any]:
    """O endereço assinado do derivado já guardado."""
    presigned = await to_thread.run_sync(
        lambda: storage.presign_get(
            bucket=bucket,
            key=key,
            version_id=ready.version_id,
            file_name="video-protegido.mp4",
            media_type=DERIVATIVE_MEDIA_TYPE,
            disposition="inline",
        )
    )
    return {
        "video": {
            "status": "ready",
            "download_url": presigned.url,
            "expires_in_seconds": presigned.expires_in_seconds,
            "byte_size": ready.size,
            "media_type": DERIVATIVE_MEDIA_TYPE,
        }
    }


async def _pdf_link_video_state(ready: Any, target: str) -> str:
    """O derivado guardado ainda corresponde ao que a origem serve?

    Três respostas, porque as duas formas de falhar são diferentes:

    - não conseguir perguntar — rede, TLS, tempo esgotado — não pode derrubar um
      vídeo pronto. O aluno recebe o que existe.
    - a origem responder que o arquivo não está mais lá é outra coisa: ali ela
      existe e falou. Servir a cópia manteria no ar algo retirado.
    """
    if ready is None:
        return "absent"
    try:
        atual = await origin_validator(target)
    except PdfLinkVideoUnavailable:
        return "current"
    except PdfLinkVideoRejected:
        return "gone"
    return "current" if ready.appsell_upload_id == atual else "stale"


def _pdf_link_video_ready(storage: S3Storage, bucket: str, key: str) -> Any:
    """O derivado já gravado, ou `None`. Nunca deixa a falha virar 'não existe'."""
    try:
        if not storage.object_exists(bucket=bucket, key=key):
            return None
        return storage.head(bucket=bucket, key=key)
    except StorageError:
        return None


async def _run_pdf_link_video_preparation(
    storage: S3Storage, bucket: str, key: str, target: str, *, replace: bool
) -> None:
    try:
        async with _PDF_LINK_VIDEO_SLOTS:
            # Outro pedido pode ter preparado enquanto este esperava a vez. Mas
            # quando o trabalho existe justamente porque o objeto guardado está
            # velho, encontrá-lo não é motivo para desistir — era assim que a
            # troca do vídeo na origem nunca chegava ao aluno.
            if (
                not replace
                and await to_thread.run_sync(lambda: _pdf_link_video_ready(storage, bucket, key))
                is not None
            ):
                return
            await _store_pdf_link_video(storage, bucket, key, target)
        logger.info("pdf_link_video_prepared", key=key)
    except PdfLinkVideoRejected:
        # Registrada também quando havia um objeto velho: sem isso, cada
        # sondagem recomeçava download e conversão de uma origem que já se sabia
        # ruim. Quem devolve o endereço ao vendedor é o prazo da recusa.
        _PDF_LINK_VIDEO_PREPARATIONS.reject(key)
        logger.warning("pdf_link_video_rejected", key=key)
    except (PdfLinkVideoUnavailable, StorageError):
        logger.warning("pdf_link_video_unavailable", key=key)
    except Exception:
        logger.exception("pdf_link_video_failed", key=key)
    finally:
        _PDF_LINK_VIDEO_PREPARATIONS.finish(key)


async def _store_pdf_link_video(storage: S3Storage, bucket: str, key: str, target: str) -> None:
    with temporary_workspace() as workspace:
        prepared = await prepare_video(target, Path(workspace))
        await to_thread.run_sync(
            lambda: storage.put_verified(
                destination_bucket=bucket,
                destination_key=key,
                source_path=prepared.path,
                byte_size=prepared.byte_size,
                sha256_hex=prepared.sha256_hex,
                content_type=DERIVATIVE_MEDIA_TYPE,
                # O metadado guarda de qual versão da origem este derivado veio, e
                # é o validador do próprio download — não o de um `HEAD` anterior,
                # que a origem pode ter mudado entre uma coisa e outra.
                upload_id=prepared.validator,
                file_name="video-protegido.mp4",
            )
        )


@router.get("/me/learning/items/{entitlement_id}/assets/{asset_id}/study")
async def read_learning_pdf_study(
    request: Request, entitlement_id: UUID, asset_id: UUID
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "learning.pdf-study.read")
    await _require_buyer_scope(internal)
    try:
        async with auth.domain_session(internal.envelope) as session:
            state = await session.scalar(
                text(
                    "select app_private.read_pdf_study_state("
                    ":identity_user_id,:entitlement_id,:asset_id)"
                ),
                {
                    "identity_user_id": internal.envelope.identity.user_id,
                    "entitlement_id": entitlement_id,
                    "asset_id": asset_id,
                },
            )
    except DBAPIError as error:
        raise _pdf_study_domain_error(error) from error
    return {"state": state}


@router.post("/me/learning/items/{entitlement_id}/assets/{asset_id}/study")
async def mutate_learning_pdf_study(
    request: Request, entitlement_id: UUID, asset_id: UUID
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "learning.pdf-study.write")
    await _require_buyer_scope(internal)
    body = await _model_from_internal_body(
        request, internal, PdfStudyMutationBody, _MAX_PDF_STUDY_BODY_BYTES
    )
    try:
        async with auth.domain_session(internal.envelope) as session:
            state = await session.scalar(
                text(
                    "select app_private.mutate_pdf_study_state("
                    ":identity_user_id,:entitlement_id,:asset_id,:expected_revision,"
                    ":idempotency_key,:body_sha256,:request_id,cast(:mutation as jsonb))"
                ),
                {
                    "identity_user_id": internal.envelope.identity.user_id,
                    "entitlement_id": entitlement_id,
                    "asset_id": asset_id,
                    "expected_revision": body.expected_revision,
                    "idempotency_key": UUID(body.idempotency_key),
                    "body_sha256": internal.envelope.body_sha256,
                    "request_id": internal.envelope.request_id,
                    "mutation": json.dumps(body.mutation.model_dump(mode="json")),
                },
            )
    except DBAPIError as error:
        raise _pdf_study_domain_error(error) from error
    return {"state": state}


@router.get("/me/learning/items/{entitlement_id}/assets/{asset_id}/pages/{page_number}")
async def render_asset_page(
    request: Request,
    entitlement_id: UUID,
    asset_id: UUID,
    page_number: int,
) -> Response:
    if not 1 <= page_number <= 100:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="protected_pdf_unavailable",
        )
    try:
        internal, manifest, row = await _authorized_pdf_page_row(
            request, entitlement_id, asset_id, page_number
        )
    except DBAPIError as error:
        raise _learning_domain_error(error) from error
    settings: Settings | None = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="pdf_viewer_unavailable",
        )
    storage: S3Storage | None = getattr(request.app.state, "storage", None)
    watermark = _protected_pdf_license_label(request, internal, entitlement_id)

    def render() -> Any:
        with authorized_pdf_page(row, settings, storage) as source:
            return render_prepared_pdf_page(
                source,
                row,
                page_count=int(manifest["page_count"]),
                watermark=watermark,
            )

    slot_acquired = False
    try:
        try:
            await asyncio.wait_for(
                _PROTECTED_PDF_SLOTS.acquire(),
                timeout=_PROTECTED_PDF_SLOT_TIMEOUT_SECONDS,
            )
            slot_acquired = True
        except TimeoutError as error:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="pdf_viewer_busy",
                headers={"retry-after": "2"},
            ) from error
        rendered = await to_thread.run_sync(render)
    except LearningPdfRejected as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="protected_pdf_unavailable",
        ) from error
    except LearningPdfUnavailable as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="pdf_viewer_unavailable",
        ) from error
    finally:
        if slot_acquired:
            _PROTECTED_PDF_SLOTS.release()
    return Response(
        rendered.payload,
        media_type="image/png",
        headers={
            "cache-control": "private, no-store, max-age=0",
            "content-disposition": f'inline; filename="page-{page_number}.png"',
            "content-security-policy": "sandbox",
            "cross-origin-resource-policy": "same-origin",
            "pragma": "no-cache",
            "referrer-policy": "no-referrer",
            "surrogate-control": "no-store",
            "vary": "x-appsell-internal-envelope",
            "x-appsell-page-count": str(rendered.page_count),
            "x-appsell-page-number": str(rendered.page_number),
            "x-appsell-sha256": rendered.sha256_hex,
            "x-content-type-options": "nosniff",
        },
    )


def _protected_pdf_license_label(
    request: Request,
    internal: AuthenticatedInternalRequest,
    entitlement_id: UUID,
) -> str:
    fallback = (
        f"Licenciado para usuário protegido • ID "
        f"{internal.envelope.identity.user_id.hex[:6]}{entitlement_id.hex[:2]}"
    )
    encoded = request.query_params.get("license")
    if encoded is None:
        return fallback
    if not 1 <= len(encoded) <= 320 or not re.fullmatch(r"[A-Za-z0-9_-]+", encoded):
        return fallback
    try:
        padding = "=" * (-len(encoded) % 4)
        label = base64.urlsafe_b64decode(encoded + padding).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return fallback
    if not label.startswith("Licenciado para ") or not 24 <= len(label) <= 160:
        return fallback
    return label


async def _authorized_pdf_manifest(
    request: Request, entitlement_id: UUID, asset_id: UUID
) -> tuple[AuthenticatedInternalRequest, Any, Any]:
    auth, internal = await _authenticate(request, "library.asset.read")
    await _require_buyer_scope(internal)
    try:
        async with auth.domain_session(internal.envelope) as session:
            manifest = (
                (
                    await session.execute(
                        text(
                            "select seller_organization_id,asset_id,asset_version,blob_id,"
                            "raster_profile,page_count,total_byte_size,manifest_sha256,pages "
                            "from app_private.authorize_entitled_pdf_manifest("
                            ":identity_user_id,:entitlement_id,:asset_id)"
                        ),
                        {
                            "identity_user_id": internal.envelope.identity.user_id,
                            "entitlement_id": entitlement_id,
                            "asset_id": asset_id,
                        },
                    )
                )
                .mappings()
                .one()
            )
            source = (
                (
                    await session.execute(
                        text(
                            "select asset_id,asset_version,file_name,media_type,byte_size,sha256,"
                            "storage_driver,storage_bucket,object_key,storage_version_id,"
                            "storage_etag from app_private.authorize_publication_asset("
                            ":identity_user_id,:entitlement_id,:asset_id)"
                        ),
                        {
                            "identity_user_id": internal.envelope.identity.user_id,
                            "entitlement_id": entitlement_id,
                            "asset_id": asset_id,
                        },
                    )
                )
                .mappings()
                .one()
            )
    except DBAPIError as error:
        raise error
    if (
        source["asset_id"] != manifest["asset_id"]
        or int(source["asset_version"]) != int(manifest["asset_version"])
        or source["media_type"] != "application/pdf"
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="pdf_manifest_source_drift",
        )
    return internal, manifest, source


async def _protected_pdf_links(
    request: Request,
    manifest: Any,
    source: Any,
) -> list[dict[str, Any]]:
    if source["storage_driver"] != "s3":
        return []
    storage: S3Storage | None = getattr(request.app.state, "storage", None)
    if storage is None:
        return []
    cache_key = f"{source['sha256']}:{source['storage_version_id']}"
    async with _PDF_LINK_CACHE_LOCK:
        cached = _PDF_LINK_CACHE.get(cache_key)
        if cached is not None:
            _PDF_LINK_CACHE.move_to_end(cache_key)
            return [dict(link) for link in cached]

    def read_links() -> dict[str, Any]:
        with storage.open_download(
            bucket=str(source["storage_bucket"]),
            key=str(source["object_key"]),
            version_id=str(source["storage_version_id"]),
        ) as pdf:
            return read_remote_pdf_link_manifest(
                pdf,
                expected_size=int(source["byte_size"]),
                expected_sha256_hex=str(source["sha256"]),
                expected_page_count=int(manifest["page_count"]),
            )

    try:
        extracted = await to_thread.run_sync(read_links)
        adjusted = tuple(_adjust_pdf_link_rectangles(extracted["links"], manifest["pages"]))
    except (
        PdfLinksRejected,
        PdfLinksUnavailable,
        StorageError,
        OSError,
        TypeError,
        ValueError,
    ):
        return []
    async with _PDF_LINK_CACHE_LOCK:
        _PDF_LINK_CACHE[cache_key] = adjusted
        _PDF_LINK_CACHE.move_to_end(cache_key)
        while len(_PDF_LINK_CACHE) > _PDF_LINK_CACHE_SIZE:
            _PDF_LINK_CACHE.popitem(last=False)
    return [dict(link) for link in adjusted]


def _adjust_pdf_link_rectangles(
    links: list[dict[str, Any]], pages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    dimensions = {
        int(page["page_number"]): (int(page["pixel_width"]), int(page["pixel_height"]))
        for page in pages
    }
    adjusted: list[dict[str, Any]] = []
    for link in links:
        width, height = dimensions[int(link["page"])]
        _, output_height, content_height, _ = license_footer_dimensions(width, height)
        content_ratio = content_height / output_height
        rect = link["rect"]
        adjusted.append(
            {
                **link,
                "rect": {
                    "left": rect["left"],
                    "top": round(rect["top"] * content_ratio, 4),
                    "width": rect["width"],
                    "height": round(rect["height"] * content_ratio, 4),
                },
            }
        )
    return adjusted


async def _authorized_pdf_page_row(
    request: Request,
    entitlement_id: UUID,
    asset_id: UUID,
    page_number: int,
) -> tuple[AuthenticatedInternalRequest, Any, Any]:
    auth, internal = await _authenticate(request, "library.asset.read")
    await _require_buyer_scope(internal)
    async with auth.domain_session(internal.envelope) as session:
        manifest = (
            (
                await session.execute(
                    text(
                        "select seller_organization_id,asset_id,asset_version,blob_id,"
                        "raster_profile,page_count,total_byte_size,manifest_sha256,pages "
                        "from app_private.authorize_entitled_pdf_manifest("
                        ":identity_user_id,:entitlement_id,:asset_id)"
                    ),
                    {
                        "identity_user_id": internal.envelope.identity.user_id,
                        "entitlement_id": entitlement_id,
                        "asset_id": asset_id,
                    },
                )
            )
            .mappings()
            .one()
        )
        row = (
            (
                await session.execute(
                    text(
                        "select seller_organization_id,asset_id,asset_version,blob_id,"
                        "raster_profile,page_number,storage_driver,storage_bucket,object_key,"
                        "storage_version_id,storage_etag,byte_size,sha256,pixel_width,"
                        "pixel_height from app_private.authorize_entitled_pdf_page("
                        ":identity_user_id,:entitlement_id,:asset_id,:page_number)"
                    ),
                    {
                        "identity_user_id": internal.envelope.identity.user_id,
                        "entitlement_id": entitlement_id,
                        "asset_id": asset_id,
                        "page_number": page_number,
                    },
                )
            )
            .mappings()
            .one()
        )
    if (
        row["asset_id"] != manifest["asset_id"]
        or row["asset_version"] != manifest["asset_version"]
        or row["blob_id"] != manifest["blob_id"]
        or int(row["page_number"]) != page_number
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="pdf_viewer_unavailable",
        )
    return internal, manifest, row


@router.get("/me/learning/items/{entitlement_id}/assets/{asset_id}/download")
async def authorize_asset_download(
    request: Request, entitlement_id: UUID, asset_id: UUID
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "library.asset.download")
    await _require_buyer_scope(internal)
    try:
        async with auth.domain_session(internal.envelope) as session:
            row = (
                (
                    await session.execute(
                        text(
                            "select asset_id,asset_version,file_name,title,media_type,byte_size,"
                            "sha256,storage_driver,storage_bucket,object_key,storage_version_id,"
                            "storage_etag from app_private.authorize_publication_asset_download("
                            ":identity_user_id,:entitlement_id,:asset_id)"
                        ),
                        {
                            "identity_user_id": internal.envelope.identity.user_id,
                            "entitlement_id": entitlement_id,
                            "asset_id": asset_id,
                        },
                    )
                )
                .mappings()
                .one()
            )
    except DBAPIError as error:
        raise _learning_domain_error(error) from error
    if row["storage_driver"] == "s3":
        storage: S3Storage | None = getattr(request.app.state, "storage", None)
        if storage is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="asset_storage_unavailable",
            )
        try:
            signed = await to_thread.run_sync(
                lambda: storage.presign_get(
                    bucket=str(row["storage_bucket"]),
                    key=str(row["object_key"]),
                    version_id=str(row["storage_version_id"]),
                    file_name=str(row["file_name"]),
                    media_type=str(row["media_type"]),
                    disposition="attachment",
                )
            )
        except (StorageError, ValueError) as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="asset_storage_unavailable",
            ) from error
        return {
            "asset": {
                "id": str(row["asset_id"]),
                "delivery": "redirect",
                "download_url": signed.url,
                "expires_in_seconds": signed.expires_in_seconds,
                "file_name": row["file_name"],
                "title": row["title"],
                "media_type": row["media_type"],
                "byte_size": row["byte_size"],
                "sha256": row["sha256"],
            }
        }
    if row["storage_driver"] != "legacy_bundle":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="asset_storage_driver_unavailable",
        )
    return {
        "asset": {
            "id": str(row["asset_id"]),
            "delivery": "legacy_bundle",
            "seed_key": str(row["file_name"]).rsplit(".", 1)[0],
            "file_name": row["file_name"],
            "title": row["title"],
            "media_type": row["media_type"],
            "byte_size": row["byte_size"],
            "sha256": row["sha256"],
        }
    }


@router.get("/me/learning/items/{entitlement_id}/resume")
async def read_learning_resume(request: Request, entitlement_id: UUID) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "learning.player.read")
    await _require_buyer_scope(internal)
    player = await _read_player(
        auth,
        internal,
        entitlement_id,
        None,
        assets_contract=request.headers.get(_LEARNING_ASSETS_CONTRACT_HEADER),
    )
    return {"player": player}


@router.get("/me/learning/items/{entitlement_id}/lessons/{lesson_id}")
async def read_learning_lesson(
    request: Request, entitlement_id: UUID, lesson_id: UUID
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "learning.player.read")
    await _require_buyer_scope(internal)
    player = await _read_player(
        auth,
        internal,
        entitlement_id,
        lesson_id,
        assets_contract=request.headers.get(_LEARNING_ASSETS_CONTRACT_HEADER),
    )
    return {"player": player}


@router.put("/me/learning/items/{entitlement_id}/lessons/{lesson_id}/progress")
async def update_learning_progress(
    request: Request, entitlement_id: UUID, lesson_id: UUID
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "learning.progress.write")
    await _require_buyer_scope(internal)
    body = await _model_from_internal_body(request, internal, LessonProgressBody)
    result = await _record_progress(
        auth,
        internal,
        entitlement_id,
        lesson_id,
        body.progress_percent,
        body.expected_version,
        UUID(body.idempotency_key),
    )
    return {"progress": result}


@router.post("/me/learning/items/{entitlement_id}/lessons/{lesson_id}/complete")
async def complete_learning_lesson(
    request: Request, entitlement_id: UUID, lesson_id: UUID
) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "learning.progress.write")
    await _require_buyer_scope(internal)
    body = await _model_from_internal_body(request, internal, LessonCompletionBody)
    result = await _record_progress(
        auth,
        internal,
        entitlement_id,
        lesson_id,
        100,
        body.expected_version,
        UUID(body.idempotency_key),
    )
    return {"progress": result}


async def _read_player(
    auth: InternalRequestAuthenticator,
    internal: AuthenticatedInternalRequest,
    entitlement_id: UUID,
    lesson_id: UUID | None,
    *,
    assets_contract: str | None = None,
) -> Any:
    if assets_contract == "v3":
        query = text(
            "select app_private.read_learning_player_v3("
            ":identity_user_id,:entitlement_id,:lesson_id)"
        )
    elif assets_contract == "v2":
        query = text(
            "select app_private.read_learning_player_v2("
            ":identity_user_id,:entitlement_id,:lesson_id)"
        )
    else:
        query = text(
            "select app_private.read_learning_player(:identity_user_id,:entitlement_id,:lesson_id)"
        )
    try:
        async with auth.domain_session(internal.envelope) as session:
            player = await session.scalar(
                query,
                {
                    "identity_user_id": internal.envelope.identity.user_id,
                    "entitlement_id": entitlement_id,
                    "lesson_id": lesson_id,
                },
            )
    except DBAPIError as error:
        raise _learning_domain_error(error) from error
    if player is None:
        raise _learning_not_found()
    return player


async def _record_progress(
    auth: InternalRequestAuthenticator,
    internal: AuthenticatedInternalRequest,
    entitlement_id: UUID,
    lesson_id: UUID,
    progress_percent: int,
    expected_version: int,
    idempotency_key: UUID,
) -> Any:
    try:
        async with auth.domain_session(internal.envelope) as session:
            player = await session.scalar(
                text(
                    "select app_private.record_lesson_progress("
                    ":identity_user_id,:entitlement_id,:lesson_id,"
                    "cast(:progress_percent as smallint),"
                    ":expected_version,:idempotency_key,:body_sha256,:request_id)"
                ),
                {
                    "identity_user_id": internal.envelope.identity.user_id,
                    "entitlement_id": entitlement_id,
                    "lesson_id": lesson_id,
                    "progress_percent": progress_percent,
                    "expected_version": expected_version,
                    "idempotency_key": idempotency_key,
                    "body_sha256": internal.envelope.body_sha256,
                    "request_id": internal.envelope.request_id,
                },
            )
    except DBAPIError as error:
        raise _learning_domain_error(error) from error
    if player is None:
        raise _learning_not_found()
    return player


async def _require_buyer_scope(internal: AuthenticatedInternalRequest) -> None:
    if internal.envelope.seller_organization_id is not None:
        await internal.consume_rejected_request()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_internal_request",
        )


async def _model_from_internal_body(
    request: Request,
    internal: AuthenticatedInternalRequest,
    model: type[BaseModel],
    max_bytes: int = _MAX_LEARNING_BODY_BYTES,
) -> Any:
    if request.headers.get("content-type") != "application/json":
        await internal.consume_rejected_request()
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="invalid_content_type",
        )
    if len(internal.body) > max_bytes:
        await internal.consume_rejected_request()
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="payload_too_large",
        )
    try:
        value = json.loads(internal.body, object_pairs_hook=_reject_duplicate_keys)
        return model.model_validate(value)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError) as error:
        await internal.consume_rejected_request()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="invalid_request_body",
        ) from error


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _canonical_uuid(value: str, label: str) -> str:
    try:
        parsed = UUID(value)
    except (AttributeError, ValueError) as error:
        raise ValueError(label) from error
    if str(parsed) != value:
        raise ValueError(label)
    return value


def _safe_sqlstate(value: object) -> str:
    """Só a forma de um SQLSTATE entra no log.

    O valor vem do driver e hoje é sempre um código de cinco caracteres — mas
    "hoje é sempre" foi exatamente a suposição que deixou passar o vazamento
    equivalente do lado do BFF. Prometer "cinco caracteres, nenhum valor" e não
    impor a forma é a mesma coisa que não prometer.
    """
    texto = "" if value is None else str(value)
    return texto if re.fullmatch(r"[0-9A-Z]{5}", texto) else "ausente_ou_invalido"


def _safe_identifier(value: str) -> str:
    """O nome de uma classe, e nada além disso."""
    return value if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", value) else "desconhecido"


def _learning_domain_error(error: DBAPIError) -> HTTPException:
    sqlstate = getattr(error.orig, "sqlstate", None)
    if sqlstate == "P4222":
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="offer_requires_checkout",
        )
    if sqlstate == "P4224":
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="access_link_exhausted",
        )
    # Revoked, expired and unknown links answer identically so that probing the
    # endpoint cannot reveal whether a token exists.
    if sqlstate == "P4225":
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="access_link_unavailable",
        )
    if sqlstate in {"28000", "42501"}:
        return _learning_not_found()
    if sqlstate == "23505":
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="idempotency_conflict",
        )
    if sqlstate == "40001":
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="progress_version_conflict",
        )
    if sqlstate == "22023":
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="invalid_domain_request",
        )
    # O sqlstate e a unica coisa que separa "o banco recusou por uma regra que
    # ainda nao mapeamos" de "o banco caiu". Sem ele, as duas viram a mesma tela
    # dizendo ao aluno para verificar a conexao — e em 30/08/2026 uma
    # investigacao inteira nao chegou ao motivo porque ele era descartado aqui.
    #
    # Cinco caracteres de codigo, nenhum valor: o sqlstate nomeia a classe do
    # erro, nao o dado que o causou.
    logger.warning(
        "learning_domain_error_unmapped",
        sqlstate=_safe_sqlstate(sqlstate),
        exception=_safe_identifier(type(getattr(error, "orig", error)).__name__),
    )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="learning_unavailable",
    )


def _pdf_study_domain_error(error: DBAPIError) -> HTTPException:
    mapped = _learning_domain_error(error)
    if (
        mapped.status_code == status.HTTP_409_CONFLICT
        and mapped.detail == "progress_version_conflict"
    ):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="pdf_study_revision_conflict",
        )
    return mapped


def _learning_not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="learning_item_unavailable",
    )
