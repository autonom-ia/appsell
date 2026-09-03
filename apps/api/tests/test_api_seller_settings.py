"""Contrato das rotas de configurações da loja (#352).

Sessão roteirizada, como em ``test_api_payout``: o que se prova é o que a
ROTA garante — capability certa por rota, parâmetros que chegam à função com
o request_id do envelope, o mapa de SQLSTATE para HTTP, e que chave e versão
de objeto nunca saem para o BFF. O banco é provado em
``test_postgres_configuracoes_da_loja``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException, status
from sqlalchemy.exc import DBAPIError

from appsell_api import seller_settings_routes
from appsell_api.public_routes import public_images
from appsell_api.seller_settings_routes import router
from appsell_api.storage_s3 import PresignedRequest, S3Storage, StorageError, StoredObject
from appsell_api.storage_worker import ScanRejected

SELLER_ID = UUID("72000000-0000-4000-8000-000000000001")
PRODUCT_ID = UUID("72000000-0000-4000-8000-000000000002")
IDENTITY_ID = UUID("71000000-0000-4000-8000-000000000001")
UPLOAD_ID = UUID("73000000-0000-4000-8000-000000000001")
QUARANTINE = "appsell-quarantine"
CHECKSUM_B64 = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
READY = "ready"
OUTRO_UPLOAD_ID = UUID("73000000-0000-4000-8000-000000000002")


class Falha(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


def db_error(sqlstate: str) -> DBAPIError:
    return DBAPIError("select 1", {}, Falha(sqlstate))


class FakeResult:
    def __init__(self, row: Any) -> None:
        self.row = row

    def one(self) -> Any:
        return self.row


class ScriptedSession:
    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _next(self, statement: Any, parameters: dict[str, Any] | None) -> Any:
        self.calls.append((str(statement), parameters or {}))
        assert self.script, "route issued more SQL than the test scripted"
        action = self.script.pop(0)
        if isinstance(action, Exception):
            raise action
        return action

    async def execute(self, statement: Any, parameters: dict[str, Any] | None = None) -> Any:
        return FakeResult(self._next(statement, parameters))

    async def scalar(self, statement: Any, parameters: dict[str, Any] | None = None) -> Any:
        return self._next(statement, parameters)


class FakeAuth:
    def __init__(self, *, seller_id: UUID | None, script: list[Any]) -> None:
        self.seller_id = seller_id
        self.session = ScriptedSession(script)
        self.capabilities: list[str] = []
        self.rejected_requests = 0
        self.consumed: list[UUID] = []
        self.request_id = uuid4()
        self.body_sha256 = "ab" * 32

    async def authenticate(self, request: Any, capability: str) -> Any:
        body = await request.body()
        self.capabilities.append(capability)
        return SimpleNamespace(
            envelope=SimpleNamespace(
                identity=SimpleNamespace(user_id=IDENTITY_ID),
                seller_organization_id=self.seller_id,
                request_id=self.request_id,
                body_sha256=self.body_sha256,
            ),
            body=body,
            consume_rejected_request=self.consume_rejected_request,
        )

    async def consume_rejected_request(self) -> None:
        self.rejected_requests += 1
        self._consume()

    def _consume(self) -> None:
        """O guarda de replay real, no falso.

        `domain_session` e `consume_rejected_request` executam o mesmo
        `app_private.consume_bff_request`: o segundo consumo do mesmo
        `request_id` viola a unicidade e vira 401. Sem isto aqui, uma rota que
        abre duas sessões passa no teste e quebra no ar — foi o que aconteceu
        na PR #389 e o que a #391 conserta.
        """
        if self.request_id in self.consumed:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="replayed")
        self.consumed.append(self.request_id)

    @property
    def domain_sessions(self) -> int:
        return len(self.consumed)

    @asynccontextmanager
    async def domain_session(self, _envelope: Any) -> AsyncIterator[ScriptedSession]:
        self._consume()
        yield self.session


class FakeStorage:
    ready_bucket = "ready"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.signed: list[tuple[str, str, str]] = []

    def presign_cover(self, *, key: str, version_id: str, media_type: str) -> PresignedRequest:
        if self.fail:
            raise StorageError("no signature today")
        self.signed.append((key, version_id, media_type))
        return PresignedRequest(
            url=f"https://cdn.test/{key}?v={version_id}", headers={}, expires_in_seconds=60
        )


@asynccontextmanager
async def client_for(
    auth: FakeAuth, storage: FakeStorage | None = None
) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(router)
    app.state.internal_auth = auth
    app.state.settings = SimpleNamespace(
        storage_quarantine_bucket=QUARANTINE, storage_ready_bucket=READY
    )
    if storage is not None:
        app.state.storage = storage
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test"
    ) as client:
        yield client


def settings_row(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "id": str(SELLER_ID),
        "slug": "hub2you",
        "display_name": "Hub2you",
        "status": "active",
        "tagline": None,
        "about": None,
        "accent_color": None,
        "logo": None,
        "updated_at": "2026-09-03T00:00:00Z",
    }
    return {**value, **overrides}


def body(**fields: Any) -> bytes:
    return json.dumps(fields, separators=(",", ":")).encode()


@pytest.mark.asyncio
async def test_ler_usa_a_capability_de_leitura_e_troca_o_logo_por_url() -> None:
    logo = {
        "object_key": f"tenants/{SELLER_ID}/logo/abc",
        "storage_version_id": "v9",
        "media_type": "image/png",
        "width": 300,
        "height": 300,
        "byte_size": 4096,
        "updated_at": "2026-09-03T00:00:00Z",
    }
    auth = FakeAuth(seller_id=SELLER_ID, script=[settings_row(logo=logo, accent_color="#1a6b52")])
    storage = FakeStorage()
    async with client_for(auth, storage) as client:
        response = await client.get(f"/v1/me/sellers/{SELLER_ID}/settings")
    assert response.status_code == 200
    assert auth.capabilities == ["seller.settings.read"]
    payload = response.json()
    assert payload["accent_color"] == "#1a6b52"
    assert payload["logo"] == {
        "url": f"https://cdn.test/tenants/{SELLER_ID}/logo/abc?v=v9",
        "media_type": "image/png",
        "width": 300,
        "height": 300,
        "expires_in_seconds": 60,
    }
    serializado = json.dumps(payload)
    assert "object_key" not in serializado and "storage_version_id" not in serializado


@pytest.mark.asyncio
async def test_ler_sem_assinatura_devolve_logo_nulo_nunca_url_quebrada() -> None:
    logo = {
        "object_key": "k",
        "storage_version_id": "v",
        "media_type": "image/png",
        "width": 64,
        "height": 64,
    }
    auth = FakeAuth(seller_id=SELLER_ID, script=[settings_row(logo=logo)])
    async with client_for(auth, FakeStorage(fail=True)) as client:
        response = await client.get(f"/v1/me/sellers/{SELLER_ID}/settings")
    assert response.status_code == 200
    assert response.json()["logo"] is None


@pytest.mark.asyncio
async def test_atualizar_manda_os_campos_e_o_request_id_do_envelope() -> None:
    auth = FakeAuth(
        seller_id=SELLER_ID,
        script=[settings_row(display_name="Hub2you", tagline="IA", changed=True)],
    )
    async with client_for(auth) as client:
        response = await client.patch(
            f"/v1/me/sellers/{SELLER_ID}/settings",
            content=body(display_name="Hub2you", tagline="IA", about=None, accent_color="#1a6b52"),
        )
    assert response.status_code == 200
    assert auth.capabilities == ["seller.settings.update"]
    sql, params = auth.session.calls[0]
    assert "update_seller_settings" in sql
    assert params["display_name"] == "Hub2you"
    assert params["tagline"] == "IA"
    assert params["about"] is None
    assert params["accent_color"] == "#1a6b52"
    assert params["request_id"] == auth.request_id
    assert response.json()["changed"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "campos",
    [
        {"display_name": "Hub2you", "accent_color": "#1A6B52"},
        {"display_name": "Hub2you", "accent_color": "1a6b52"},
        {"display_name": " Hub2you"},
        {"display_name": "Hub2you", "tagline": "x"},
        {"display_name": "Hub2you", "slug": "novo-slug"},
        {"display_name": "Hub2you", "about": "com\tcontrole"},
    ],
)
async def test_atualizar_recusa_corpo_invalido_antes_do_banco(campos: dict[str, Any]) -> None:
    auth = FakeAuth(seller_id=SELLER_ID, script=[])
    async with client_for(auth) as client:
        response = await client.patch(
            f"/v1/me/sellers/{SELLER_ID}/settings", content=body(**campos)
        )
    assert response.status_code == 422
    assert auth.session.calls == []
    assert auth.rejected_requests == 1


@pytest.mark.asyncio
async def test_atualizar_de_outra_loja_no_envelope_e_401() -> None:
    auth = FakeAuth(seller_id=uuid4(), script=[])
    async with client_for(auth) as client:
        response = await client.patch(
            f"/v1/me/sellers/{SELLER_ID}/settings", content=body(display_name="Hub2you")
        )
    assert response.status_code == 401
    assert auth.session.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sqlstate", "http", "detail"),
    [
        ("42501", 403, "forbidden"),
        ("22023", 422, None),
        ("P4041", 404, None),
        ("55000", 409, "seller_inactive"),
    ],
)
async def test_sqlstate_vira_http(sqlstate: str, http: int, detail: str | None) -> None:
    auth = FakeAuth(seller_id=SELLER_ID, script=[db_error(sqlstate)])
    async with client_for(auth) as client:
        response = await client.patch(
            f"/v1/me/sellers/{SELLER_ID}/settings", content=body(display_name="Hub2you")
        )
    assert response.status_code == http, response.text
    if detail:
        assert response.json()["detail"] == detail


@pytest.mark.asyncio
async def test_promessa_usa_a_capability_de_rascunho_e_a_versao_esperada() -> None:
    auth = FakeAuth(seller_id=SELLER_ID, script=[SimpleNamespace(product_version=8, changed=True)])
    async with client_for(auth) as client:
        response = await client.patch(
            f"/v1/me/sellers/{SELLER_ID}/products/{PRODUCT_ID}/headline",
            content=body(
                idempotency_key=str(uuid4()),
                expected_product_version=7,
                headline="Feche mais apólices",
            ),
        )
    assert response.status_code == 200
    assert auth.capabilities == ["catalog.draft.update"]
    sql, params = auth.session.calls[0]
    assert "update_product_headline" in sql
    assert params["headline"] == "Feche mais apólices"
    assert params["expected_product_version"] == 7
    assert params["body_sha256"] == auth.body_sha256
    assert response.json() == {
        "product_version": 8,
        "changed": True,
        "headline": "Feche mais apólices",
    }


@pytest.mark.asyncio
async def test_promessa_com_versao_velha_e_409() -> None:
    auth = FakeAuth(seller_id=SELLER_ID, script=[db_error("40001")])
    async with client_for(auth) as client:
        response = await client.patch(
            f"/v1/me/sellers/{SELLER_ID}/products/{PRODUCT_ID}/headline",
            content=body(idempotency_key=str(uuid4()), expected_product_version=1, headline=None),
        )
    assert response.status_code == 409
    assert response.json()["detail"] == "draft_version_conflict"


@pytest.mark.asyncio
async def test_logo_intent_sem_armazenamento_e_503_e_nao_toca_o_banco() -> None:
    auth = FakeAuth(seller_id=SELLER_ID, script=[])
    async with client_for(auth) as client:
        response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/settings/logo/upload-intents",
            content=body(
                idempotency_key=str(uuid4()),
                media_type="image/png",
                byte_size=4096,
                sha256="ab" * 32,
            ),
        )
    assert response.status_code == 503
    assert response.json()["detail"] == "logo_storage_unavailable"
    assert auth.session.calls == []


@pytest.mark.asyncio
async def test_tipo_de_imagem_desconhecido_e_404_antes_do_banco() -> None:
    auth = FakeAuth(seller_id=SELLER_ID, script=[])
    async with client_for(auth, FakeStorage()) as client:
        response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/settings/avatar/upload-intents",
            content=body(
                idempotency_key=str(uuid4()),
                media_type="image/png",
                byte_size=4096,
                sha256="ab" * 32,
            ),
        )
    assert response.status_code == 404
    assert response.json()["detail"] == "unknown_image_kind"
    assert auth.session.calls == []
    assert auth.rejected_requests == 1


@pytest.mark.asyncio
async def test_banner_clear_passa_o_tipo_ao_banco_e_apaga_a_chave_do_banner() -> None:
    class Storage(FakeStorage):
        def __init__(self) -> None:
            super().__init__()
            self.deleted: list[tuple[str, str]] = []

        def delete_all_versions(self, *, bucket: str, key: str) -> None:
            self.deleted.append((bucket, key))

    storage = Storage()
    auth = FakeAuth(
        seller_id=SELLER_ID,
        script=[SimpleNamespace(changed=True, previous_object_key="tenants/x/banner/old")],
    )
    async with client_for(auth, storage) as client:
        response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/settings/banner/clear",
            content=body(idempotency_key=str(uuid4())),
        )
    assert response.status_code == 200
    assert response.json() == {"changed": True}
    assert storage.deleted == [("ready", "tenants/x/banner/old")]
    (statement, parameters) = auth.session.calls[0]
    assert "clear_seller_image(" in statement
    assert parameters["kind"] == "banner"


@pytest.mark.asyncio
async def test_confirm_recusa_objeto_de_outro_upload_sem_tocar_em_nada() -> None:
    """O objeto na quarentena carrega o id de quem o enviou. Confirmar com o id
    de outro upload (outro tipo, ou um já substituído) para antes de qualquer
    ação destrutiva: quem está enviando de verdade não perde o arquivo."""

    class Storage(FakeStorage):
        def __init__(self) -> None:
            super().__init__()
            self.deleted: list[tuple[str, str]] = []

        def head(self, *, bucket: str, key: str, version_id: str | None = None) -> StoredObject:
            return StoredObject(
                bucket=bucket,
                key=key,
                version_id="v9",
                size=4096,
                content_type="image/png",
                checksum_sha256_b64="",
                etag="e",
                appsell_upload_id=str(OUTRO_UPLOAD_ID),
            )

        def delete_all_versions(self, *, bucket: str, key: str) -> None:
            self.deleted.append((bucket, key))

    storage = Storage()
    auth = FakeAuth(seller_id=SELLER_ID, script=[])
    async with client_for(auth, storage) as client:
        response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/settings/banner/uploads/{UPLOAD_ID}/confirm",
            content=body(idempotency_key=str(uuid4())),
        )
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "logo_upload_superseded"
    assert storage.deleted == []
    assert auth.session.calls == []
    assert auth.rejected_requests == 1


@pytest.mark.asyncio
class ImagemVerificada:
    """O que a rota lê do resultado da verificação, e nada mais."""

    media_type = "image/webp"
    sha256_hex = "cd" * 32
    width = 1600
    height = 500
    byte_size = 4096
    ready = SimpleNamespace(version_id="ready-v1")


class WorkerFalso:
    """O verificador, sem baixar nem escanear nada.

    A rota constrói o worker por dentro; o teste troca a classe no módulo. O
    que importa aqui não é a verificação — é quantas sessões de domínio a rota
    abre em cada desfecho, e qual limpeza ela chama.
    """

    ultimo: WorkerFalso | None = None

    def __init__(self, storage: Any, scanner: Any, *, rejeita: bool = False) -> None:
        self.rejeita = rejeita
        self.limpou_origem = False
        self.descartou_pronto = False
        self.descartou_quarentena: list[str] = []
        WorkerFalso.ultimo = self

    def process(self, job: Any) -> Any:
        if self.rejeita:
            raise ScanRejected("recusado no teste")
        return ImagemVerificada()

    def cleanup_source_after_commit(self, verified: Any) -> None:
        self.limpou_origem = True

    def discard_ready_cover(self, verified: Any) -> None:
        self.descartou_pronto = True

    def discard_abandoned_image(self, *, seller_id: UUID, kind: str) -> None:
        self.descartou_quarentena.append(kind)


class StorageQueVe(FakeStorage):
    """Quarentena com o objeto do upload que a rota está confirmando."""

    def __init__(self, upload_id: UUID = UPLOAD_ID) -> None:
        super().__init__()
        self.upload_id = upload_id
        self.deleted: list[tuple[str, str]] = []

    def head(self, *, bucket: str, key: str, version_id: str | None = None) -> StoredObject:
        return StoredObject(
            bucket=bucket,
            key=key,
            version_id="quarantine-v1",
            size=4096,
            content_type="image/png",
            checksum_sha256_b64=CHECKSUM_B64,
            etag="e",
            appsell_upload_id=str(self.upload_id),
        )

    def delete_all_versions(self, *, bucket: str, key: str) -> None:
        self.deleted.append((bucket, key))


def _troca_o_worker(monkeypatch: pytest.MonkeyPatch, *, rejeita: bool = False) -> None:
    monkeypatch.setattr(
        seller_settings_routes,
        "ProductCoverWorker",
        lambda storage, scanner: WorkerFalso(storage, scanner, rejeita=rejeita),
    )
    monkeypatch.setattr(
        seller_settings_routes, "_scanner", lambda request, internal: _pronto(object())
    )


async def _pronto(value: Any) -> Any:
    return value


@pytest.mark.asyncio
async def test_confirm_feliz_grava_e_abre_uma_unica_sessao(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O caminho que o incidente derrubou: uma sessão, gravação, 200."""
    _troca_o_worker(monkeypatch)
    auth = FakeAuth(seller_id=SELLER_ID, script=[SimpleNamespace(previous_object_key=None)])
    async with client_for(auth, StorageQueVe()) as client:
        response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/settings/banner/uploads/{UPLOAD_ID}/confirm",
            content=body(idempotency_key=str(uuid4())),
        )
    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "banner"
    assert auth.domain_sessions == 1
    (statement, parameters) = auth.session.calls[0]
    assert "set_seller_image(" in statement and parameters["kind"] == "banner"
    assert WorkerFalso.ultimo is not None and WorkerFalso.ultimo.limpou_origem


@pytest.mark.asyncio
async def test_confirm_recusado_fecha_o_upload_em_uma_sessao(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rejeição do verificador: fecha o upload e limpa, ainda em uma sessão."""
    _troca_o_worker(monkeypatch, rejeita=True)
    auth = FakeAuth(
        seller_id=SELLER_ID,
        script=[SimpleNamespace(upload_id=str(UPLOAD_ID), status="rejected", changed=True)],
    )
    async with client_for(auth, StorageQueVe()) as client:
        response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/settings/banner/uploads/{UPLOAD_ID}/confirm",
            content=body(idempotency_key=str(uuid4())),
        )
    assert response.status_code == 422
    assert response.json()["detail"] == "logo_rejected"
    assert auth.domain_sessions == 1
    (statement, parameters) = auth.session.calls[0]
    assert "close_seller_image_upload(" in statement and parameters["kind"] == "banner"
    assert WorkerFalso.ultimo is not None
    assert WorkerFalso.ultimo.descartou_quarentena == ["banner"]


@pytest.mark.asyncio
async def test_confirm_com_falha_na_gravacao_descarta_o_pronto_em_uma_sessao(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Se a gravação falha, o objeto promovido sai do bucket servido."""
    _troca_o_worker(monkeypatch)
    auth = FakeAuth(seller_id=SELLER_ID, script=[db_error("55000")])
    async with client_for(auth, StorageQueVe()) as client:
        response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/settings/banner/uploads/{UPLOAD_ID}/confirm",
            content=body(idempotency_key=str(uuid4())),
        )
    assert response.status_code == 409
    assert response.json()["detail"] == "seller_inactive"
    assert auth.domain_sessions == 1
    assert WorkerFalso.ultimo is not None and WorkerFalso.ultimo.descartou_pronto


@pytest.mark.asyncio
async def test_logo_clear_apaga_a_chave_anterior_do_bucket() -> None:
    class Storage(FakeStorage):
        def __init__(self) -> None:
            super().__init__()
            self.deleted: list[tuple[str, str]] = []

        def delete_all_versions(self, *, bucket: str, key: str) -> None:
            self.deleted.append((bucket, key))

    storage = Storage()
    auth = FakeAuth(
        seller_id=SELLER_ID,
        script=[SimpleNamespace(changed=True, previous_object_key="tenants/x/logo/old")],
    )
    async with client_for(auth, storage) as client:
        response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/settings/logo/clear",
            content=body(idempotency_key=str(uuid4())),
        )
    assert response.status_code == 200
    assert response.json() == {"changed": True}
    assert storage.deleted == [("ready", "tenants/x/logo/old")]


# ------------------------------------------------------------ rota pública


def test_vitrine_publica_troca_chave_por_url_e_some_sem_armazenamento() -> None:
    projection = {
        "schema_version": 1,
        "seller": {
            "id": str(SELLER_ID),
            "slug": "hub2you",
            "name": "Hub2you",
            "accent_color": "#1a6b52",
            "logo": {
                "object_key": "tenants/s/logo/1",
                "storage_version_id": "v1",
                "media_type": "image/png",
                "width": 300,
                "height": 300,
            },
            "banner": {
                "object_key": "tenants/s/banner/2",
                "storage_version_id": "v3",
                "media_type": "image/jpeg",
                "width": 1600,
                "height": 500,
            },
        },
        "products": [
            {
                "id": "p1",
                "cover": {
                    "object_key": "tenants/s/covers/p1/u",
                    "storage_version_id": "v2",
                    "media_type": "image/webp",
                    "width": 800,
                    "height": 450,
                },
            },
            {"id": "p2"},
        ],
    }
    storage = FakeStorage()
    signed = public_images(projection, cast(S3Storage, storage))
    assert signed["seller"]["logo"] == {
        "url": "https://cdn.test/tenants/s/logo/1?v=v1",
        "media_type": "image/png",
        "width": 300,
        "height": 300,
        "expires_in_seconds": 60,
    }
    assert signed["products"][0]["cover"]["url"].startswith(
        "https://cdn.test/tenants/s/covers/p1/u"
    )
    assert "cover" not in signed["products"][1]
    assert signed["seller"]["accent_color"] == "#1a6b52"
    texto = json.dumps(signed)
    assert "object_key" not in texto and "storage_version_id" not in texto

    assert signed["seller"]["banner"]["url"].startswith("https://cdn.test/tenants/s/banner/2")
    assert signed["seller"]["banner"]["width"] == 1600

    sem_storage = public_images(projection, None)
    assert "logo" not in sem_storage["seller"]
    assert "banner" not in sem_storage["seller"]
    assert "cover" not in sem_storage["products"][0]
    assert sem_storage["seller"]["name"] == "Hub2you"

    falhando = public_images(projection, cast(S3Storage, FakeStorage(fail=True)))
    assert "logo" not in falhando["seller"]

    # Uma loja sem marca sai exatamente como entrou: é o contrato do web antigo.
    limpo = {"schema_version": 1, "seller": {"id": "x", "slug": "y", "name": "z"}, "products": []}
    assert public_images(limpo, cast(S3Storage, storage)) == limpo
