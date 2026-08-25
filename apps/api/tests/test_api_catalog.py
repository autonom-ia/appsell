from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from appsell_api.catalog_routes import _domain_error, _product_detail_response, router
from appsell_api.config import get_settings
from appsell_api.main import app as domain_app

SELLER_ID = UUID("52000000-0000-4000-8000-000000000001")
OTHER_SELLER_ID = UUID("52000000-0000-4000-8000-000000000002")
IDENTITY_ID = UUID("51000000-0000-4000-8000-000000000001")
RUNTIME_URL = os.environ.get("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY_HEX = os.environ.get("APPSELL_TEST_CONTEXT_KEY_HEX")


class FakeResult:
    def __init__(
        self, *, one: dict[str, Any] | None = None, all: list[dict[str, Any]] | None = None
    ):
        self._one = one
        self._all = all or []

    def mappings(self) -> FakeResult:
        return self

    def one(self) -> dict[str, Any]:
        assert self._one is not None
        return self._one

    def one_or_none(self) -> dict[str, Any] | None:
        return self._one

    def all(self) -> list[dict[str, Any]]:
        return self._all or ([self._one] if self._one is not None else [])


class FakeSession:
    def __init__(self, result: FakeResult):
        self.result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, parameters: dict[str, Any]) -> FakeResult:
        self.calls.append((str(statement), parameters))
        return self.result


class FakeAuth:
    def __init__(
        self,
        *,
        seller_id: UUID | None,
        result: FakeResult,
        auth_error: HTTPException | None = None,
    ) -> None:
        self.seller_id = seller_id
        self.session = FakeSession(result)
        self.capabilities: list[str] = []
        self.domain_sessions = 0
        self.rejected_requests = 0
        self.auth_error = auth_error

    async def authenticate(self, request: Any, capability: str) -> Any:
        if self.auth_error is not None:
            raise self.auth_error
        body = await request.body()
        self.capabilities.append(capability)
        return SimpleNamespace(
            envelope=SimpleNamespace(
                identity=SimpleNamespace(user_id=IDENTITY_ID),
                seller_organization_id=self.seller_id,
                body_sha256=hashlib.sha256(body).hexdigest(),
                request_id=uuid4(),
            ),
            body=body,
            consume_rejected_request=self.consume_rejected_request,
        )

    async def consume_rejected_request(self) -> None:
        self.rejected_requests += 1

    @asynccontextmanager
    async def domain_session(self, _envelope: Any) -> AsyncIterator[FakeSession]:
        self.domain_sessions += 1
        yield self.session


@asynccontextmanager
async def client_for(
    auth: FakeAuth, *, capacity_product_create_telemetry: bool = False
) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(router)
    app.state.internal_auth = auth
    app.state.settings = SimpleNamespace(
        capacity_product_create_telemetry=capacity_product_create_telemetry
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test"
    ) as client:
        yield client


def draft_body(**overrides: Any) -> bytes:
    value = {
        "idempotency_key": str(uuid4()),
        "slug": "produto-inicial",
        "title": "Produto Inicial",
        "description": "Rascunho privado",
        "course_title": "Curso Inicial",
        **overrides,
    }
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()


def update_body(course_id: UUID, offer_id: UUID, **overrides: Any) -> bytes:
    value = {
        "idempotency_key": str(uuid4()),
        "course_id": str(course_id),
        "offer_id": str(offer_id),
        "slug": "produto-editado",
        "title": "Produto Editado",
        "description": "Rascunho persistido",
        "course_title": "Curso Editado",
        "offer_title": "Oferta Principal",
        "offer_summary": "Condições informativas sem billing",
        "offer_unit_amount_minor": 19900,
        "offer_currency": "BRL",
        "offer_billing_period": "one_time",
        "expected_product_version": 1,
        "expected_course_version": 1,
        "expected_offer_version": 0,
        **overrides,
    }
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()


def product_course_update_body(course_id: UUID, **overrides: Any) -> bytes:
    value = {
        "idempotency_key": str(uuid4()),
        "course_id": str(course_id),
        "slug": "produto-editorial",
        "title": "Produto Editorial",
        "description": "Descrição editorial",
        "course_title": "Curso Editorial",
        "expected_product_version": 1,
        "expected_course_version": 1,
        **overrides,
    }
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()


def publication_body(course_id: UUID, offer_id: UUID, **overrides: Any) -> bytes:
    value = {
        "idempotency_key": str(uuid4()),
        "offer_id": str(offer_id),
        "course_versions": [{"id": str(course_id), "version": 2}],
        "expected_product_version": 2,
        "expected_offer_version": 1,
        **overrides,
    }
    return json.dumps(value, separators=(",", ":")).encode()


def offer_status_body(**overrides: Any) -> bytes:
    value = {
        "idempotency_key": str(uuid4()),
        "expected_status": "draft",
        "status": "active",
        "expected_offer_version": 1,
        **overrides,
    }
    return json.dumps(value, separators=(",", ":")).encode()


def curriculum_body(**overrides: Any) -> bytes:
    value = {
        "idempotency_key": str(uuid4()),
        "expected_course_version": 1,
        **overrides,
    }
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()


@pytest.mark.asyncio
async def test_create_draft_binds_seller_actor_hash_and_idempotency() -> None:
    product_id, course_id = uuid4(), uuid4()
    auth = FakeAuth(
        seller_id=SELLER_ID,
        result=FakeResult(
            one={
                "catalog_product_id": product_id,
                "course_id": course_id,
                "created": True,
            }
        ),
    )
    body = draft_body()

    async with client_for(auth) as client:
        response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/products",
            content=body,
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 201
    assert response.json() == {
        "product_id": str(product_id),
        "course_id": str(course_id),
        "status": "draft",
        "created": True,
    }
    assert auth.capabilities == ["catalog.draft.create"]
    assert auth.domain_sessions == 1
    statement, parameters = auth.session.calls[0]
    assert "app_private.create_product_course_draft" in statement
    assert parameters["identity_user_id"] == IDENTITY_ID
    assert parameters["seller_id"] == SELLER_ID
    assert parameters["body_sha256"] == hashlib.sha256(body).hexdigest()
    assert isinstance(parameters["idempotency_key"], UUID)


@pytest.mark.asyncio
async def test_create_idempotent_replay_returns_200() -> None:
    auth = FakeAuth(
        seller_id=SELLER_ID,
        result=FakeResult(
            one={
                "catalog_product_id": uuid4(),
                "course_id": uuid4(),
                "created": False,
            }
        ),
    )
    async with client_for(auth) as client:
        response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/products",
            content=draft_body(),
            headers={"content-type": "application/json"},
        )
    assert response.status_code == 200
    assert response.json()["created"] is False


@pytest.mark.asyncio
async def test_create_capacity_trace_emits_allowlisted_completion(
    capsys: pytest.CaptureFixture[str],
) -> None:
    auth = FakeAuth(
        seller_id=SELLER_ID,
        result=FakeResult(
            one={
                "catalog_product_id": uuid4(),
                "course_id": uuid4(),
                "created": True,
            }
        ),
    )
    async with client_for(auth, capacity_product_create_telemetry=True) as client:
        response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/products",
            content=draft_body(),
            headers={
                "content-type": "application/json",
                "x-appsell-capacity-trace": "pc-007",
            },
        )
    assert response.status_code == 201
    event = json.loads(capsys.readouterr().out)
    assert event["layer"] == "api"
    assert event["trace"] == "pc-007"
    assert event["status"] == 201
    assert event["outcome"] == "success"
    assert set(event["durations_ms"]) == {
        "internal_auth_verification",
        "pool_checkout_replay_apply_context",
        "sql",
        "commit_response",
    }
    assert abs(event["total_ms"] - sum(event["durations_ms"].values())) <= 1


@pytest.mark.asyncio
async def test_capacity_trace_preserves_internal_auth_failure_completion(
    capsys: pytest.CaptureFixture[str],
) -> None:
    auth = FakeAuth(
        seller_id=SELLER_ID,
        result=FakeResult(),
        auth_error=HTTPException(status_code=401, detail="invalid_internal_request"),
    )
    async with client_for(auth, capacity_product_create_telemetry=True) as client:
        response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/products",
            content=draft_body(),
            headers={
                "content-type": "application/json",
                "x-appsell-capacity-trace": "pc-008",
            },
        )
    assert response.status_code == 401
    event = json.loads(capsys.readouterr().out)
    assert event["status"] == 401
    assert event["outcome"] == "http_error"
    assert event["durations_ms"]["internal_auth_verification"] >= 0
    assert event["durations_ms"]["pool_checkout_replay_apply_context"] == 0
    assert abs(event["total_ms"] - sum(event["durations_ms"].values())) <= 1


@pytest.mark.asyncio
async def test_envelope_seller_must_match_path_before_domain_access() -> None:
    auth = FakeAuth(seller_id=OTHER_SELLER_ID, result=FakeResult())
    async with client_for(auth) as client:
        create = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/products",
            content=draft_body(),
            headers={"content-type": "application/json"},
        )
        listing = await client.get(f"/v1/me/sellers/{SELLER_ID}/products")
    assert create.status_code == 401
    assert listing.status_code == 401
    assert auth.domain_sessions == 0
    assert auth.session.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        draft_body(idempotency_key="not-a-uuid"),
        draft_body(slug="UPPERCASE"),
        draft_body(title=" título com espaço"),
        draft_body(course_title="x"),
        draft_body(description="a" * 2001),
        draft_body(unexpected="field"),
        b'{"idempotency_key":"52000000-0000-4000-8000-000000000001",'
        b'"slug":"primeiro","slug":"segundo","title":"Produto",'
        b'"description":"","course_title":"Curso"}',
    ],
)
async def test_invalid_or_ambiguous_create_body_is_rejected(body: bytes) -> None:
    auth = FakeAuth(seller_id=SELLER_ID, result=FakeResult())
    async with client_for(auth) as client:
        response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/products",
            content=body,
            headers={"content-type": "application/json"},
        )
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request_body"}
    assert auth.session.calls == []


@pytest.mark.asyncio
async def test_list_drafts_is_tenant_scoped_and_uses_bounded_keyset() -> None:
    now = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
    product_ids = [uuid4(), uuid4(), uuid4()]
    rows = [
        {
            "id": product_id,
            "slug": f"produto-{index}",
            "title": f"Produto {index}",
            "description": "Privado",
            "status": "draft",
            "version": 1,
            "created_at": now,
            "updated_at": now,
            "course_id": uuid4(),
            "course_title": f"Curso {index}",
            "course_status": "draft",
            "course_draft_version": 1,
            "cover_object_key": None,
            "cover_storage_version_id": None,
            "cover_media_type": None,
            "cover_width": None,
            "cover_height": None,
            "students": 0,
        }
        for index, product_id in enumerate(product_ids)
    ]
    auth = FakeAuth(seller_id=SELLER_ID, result=FakeResult(all=rows))
    cursor = uuid4()

    async with client_for(auth) as client:
        response = await client.get(f"/v1/me/sellers/{SELLER_ID}/products?limit=2&cursor={cursor}")

    assert response.status_code == 200
    payload = response.json()
    assert [product["id"] for product in payload["products"]] == [
        str(product_ids[0]),
        str(product_ids[1]),
    ]
    assert payload["next_cursor"] == str(product_ids[1])
    assert payload["products"][0]["course"]["status"] == "draft"
    assert auth.capabilities == ["catalog.draft.read"]
    statement, parameters = auth.session.calls[0]
    assert "product.status <> 'archived'" in statement
    assert "course.status <> 'archived'" in statement
    assert "order by product.created_at desc, product.id desc" in statement
    # O ator entra nos parâmetros porque a contagem de alunos atravessa a
    # fronteira da matrícula e confere o contexto da transação contra ele.
    assert parameters == {
        "identity_user_id": IDENTITY_ID,
        "seller_id": SELLER_ID,
        "cursor": cursor,
        "fetch_limit": 3,
    }


@pytest.mark.asyncio
async def test_list_limit_and_cursor_are_validated_before_authentication() -> None:
    auth = FakeAuth(seller_id=SELLER_ID, result=FakeResult())
    async with client_for(auth) as client:
        too_large = await client.get(f"/v1/me/sellers/{SELLER_ID}/products?limit=101")
        invalid_cursor = await client.get(f"/v1/me/sellers/{SELLER_ID}/products?cursor=not-a-uuid")
    assert too_large.status_code == 422
    assert invalid_cursor.status_code == 422
    assert auth.capabilities == []


def test_domain_errors_are_stable_and_do_not_leak_driver_details() -> None:
    class DriverError:
        def __init__(
            self,
            sqlstate: str | None,
            constraint_name: str | None = None,
            message: str | None = None,
            diagnostic_message: str | None = None,
        ):
            self.sqlstate = sqlstate
            self.constraint_name = constraint_name
            self.message = message
            self.diag = SimpleNamespace(message_primary=diagnostic_message)

    for sqlstate, expected_status, expected_detail in [
        ("23505", 409, "resource_conflict"),
        ("40001", 409, "draft_version_conflict"),
        ("P4091", 409, "no_unpublished_changes"),
        ("P4092", 409, "offer_status_conflict"),
        ("P4221", 422, "snapshot_too_large"),
        ("P4222", 422, "invalid_offer_status_transition"),
        ("22023", 422, "invalid_domain_request"),
        ("55000", 422, "publication_not_ready"),
        ("28000", 403, "forbidden"),
        ("42501", 403, "forbidden"),
        (None, 503, "domain_unavailable"),
    ]:
        mapped = _domain_error(SimpleNamespace(orig=DriverError(sqlstate)))  # type: ignore[arg-type]
        assert mapped.status_code == expected_status
        assert mapped.detail == expected_detail
        assert "driver" not in str(mapped.detail)

    slug = _domain_error(
        SimpleNamespace(  # type: ignore[arg-type]
            orig=DriverError("23505", "catalog_product_seller_slug_key")
        )
    )
    assert (slug.status_code, slug.detail) == (409, "slug_conflict")


@pytest.mark.asyncio
async def test_detail_and_preview_are_private_server_projections() -> None:
    now = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
    product_id, course_id, offer_id = uuid4(), uuid4(), uuid4()
    row = {
        "seller_organization_id": SELLER_ID,
        "id": product_id,
        "slug": "produto-editado",
        "title": "Produto Editado",
        "description": "Privado",
        "status": "draft",
        "version": 2,
        "created_at": now,
        "updated_at": now,
        "published_snapshot_id": None,
        "cover_object_key": None,
        "cover_storage_version_id": None,
        "cover_media_type": None,
        "cover_width": None,
        "cover_height": None,
        "course_id": course_id,
        "course_title": "Curso Editado",
        "course_description": "",
        "course_status": "draft",
        "course_draft_version": 2,
        "course_position": 0,
        "curriculum_modules": [
            {
                "id": str(uuid4()),
                "title": "Fundamentos",
                "status": "active",
                "position": 0,
                "lessons": [
                    {
                        "id": str(uuid4()),
                        "title": "Primeira aula",
                        "kind": "text",
                        "content": "Conteúdo",
                        "status": "active",
                        "position": 0,
                        "assets": [
                            {
                                "id": str(uuid4()),
                                "version": 1,
                                "title": "Guia de boas-vindas",
                                "file_name": "arquivo-fisico.pdf",
                                "media_type": "application/pdf",
                                "byte_size": 2_000_000,
                                "position": 0,
                            }
                        ],
                    }
                ],
            }
        ],
        "offer_id": offer_id,
        "offer_title": "Oferta Principal",
        "offer_summary": "Sem billing",
        "offer_status": "draft",
        "offer_version": 1,
        "offer_unit_amount_minor": 19900,
        "offer_currency": "BRL",
        "offer_billing_period": "one_time",
        "publication_version": None,
        "publication_source_sha256": None,
        "published_at": None,
        "publication_snapshot": None,
    }
    second_course_id = uuid4()
    second_row = {
        **row,
        "course_id": second_course_id,
        "course_title": "Curso Avançado",
        "course_draft_version": 3,
        "course_position": 1,
    }
    auth = FakeAuth(seller_id=SELLER_ID, result=FakeResult(all=[row, second_row]))
    async with client_for(auth) as client:
        detail = await client.get(f"/v1/me/sellers/{SELLER_ID}/products/{product_id}")
        preview = await client.get(f"/v1/me/sellers/{SELLER_ID}/products/{product_id}/preview")
    assert detail.status_code == 200
    assert detail.json()["product"]["readiness"]["can_publish"] is True
    assert detail.json()["product"]["offer"]["id"] == str(offer_id)
    assert detail.json()["product"]["curriculum"] == {"modules": 2, "lessons": 2}
    assert [course["id"] for course in detail.json()["product"]["courses"]] == [
        str(course_id),
        str(second_course_id),
    ]
    assert preview.status_code == 200
    assert preview.json()["preview"] is True
    assert preview.json()["snapshot"]["product"]["id"] == str(product_id)
    assert len(preview.json()["snapshot"]["courses"]) == 2
    preview_asset = preview.json()["snapshot"]["courses"][0]["modules"][0]["lessons"][0]["assets"][
        0
    ]
    assert preview_asset["title"] == "Guia de boas-vindas"
    assert preview_asset["file_name"] == "arquivo-fisico.pdf"
    detail_sql, detail_parameters = auth.session.calls[0]
    preview_sql, preview_parameters = auth.session.calls[1]
    for statement in (detail_sql, preview_sql):
        assert "app_private.list_product_draft_lesson_assets" in statement
        assert "from lesson_asset_draft draft" not in statement
    assert detail_parameters["capability"] == "catalog.draft.read"
    assert preview_parameters["capability"] == "catalog.draft.preview"
    assert auth.capabilities == ["catalog.draft.read", "catalog.draft.preview"]


def test_readiness_blocks_publication_while_an_asset_upload_is_pending() -> None:
    now = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
    row = {
        "seller_organization_id": SELLER_ID,
        "id": uuid4(),
        "slug": "produto-com-material-pendente",
        "title": "Produto com material pendente",
        "description": "Privado",
        "status": "draft",
        "version": 2,
        "created_at": now,
        "updated_at": now,
        "published_snapshot_id": None,
        "course_id": uuid4(),
        "course_title": "Curso",
        "course_description": "",
        "course_status": "draft",
        "course_draft_version": 2,
        "course_position": 0,
        "curriculum_modules": [],
        "offer_id": uuid4(),
        "offer_title": "Oferta",
        "offer_summary": "Informativa",
        "offer_status": "draft",
        "offer_version": 1,
        "offer_unit_amount_minor": 19900,
        "offer_currency": "BRL",
        "offer_billing_period": "one_time",
        "publication_version": None,
        "publication_source_sha256": None,
        "published_at": None,
        "publication_snapshot": None,
        "has_pending_asset_upload": True,
        "cover_object_key": None,
        "cover_storage_version_id": None,
        "cover_media_type": None,
        "cover_width": None,
        "cover_height": None,
    }

    product = _product_detail_response([row], None)["product"]

    assert product["readiness"] == {
        "can_publish": False,
        "items": [
            {
                "key": "asset_upload_pending",
                "label": "Aguarde a verificação dos materiais antes de publicar o catálogo.",
                "ready": False,
            }
        ],
    }


@pytest.mark.asyncio
async def test_patch_binds_versions_ids_hash_and_actor_to_atomic_function() -> None:
    product_id, course_id, offer_id = uuid4(), uuid4(), uuid4()
    auth = FakeAuth(
        seller_id=SELLER_ID,
        result=FakeResult(
            one={
                "offer_id": offer_id,
                "product_version": 2,
                "course_version": 2,
                "offer_version": 1,
                "created": True,
            }
        ),
    )
    body = update_body(course_id, offer_id)
    async with client_for(auth) as client:
        response = await client.patch(
            f"/v1/me/sellers/{SELLER_ID}/products/{product_id}", content=body
        )
    assert response.status_code == 200
    assert response.json()["offer_version"] == 1
    assert auth.capabilities == ["catalog.draft.update"]
    statement, parameters = auth.session.calls[0]
    assert "app_private.update_product_course_offer_draft" in statement
    assert parameters["product_id"] == product_id
    assert parameters["course_id"] == course_id
    assert parameters["offer_id"] == offer_id
    assert parameters["offer_unit_amount_minor"] == 19900
    assert parameters["offer_currency"] == "BRL"
    assert parameters["offer_billing_period"] == "one_time"
    assert parameters["body_sha256"] == hashlib.sha256(body).hexdigest()


@pytest.mark.asyncio
async def test_patch_product_course_draft_does_not_require_offer() -> None:
    product_id, course_id = uuid4(), uuid4()
    auth = FakeAuth(
        seller_id=SELLER_ID,
        result=FakeResult(
            one={
                "product_version": 2,
                "course_version": 2,
                "updated": True,
            }
        ),
    )
    body = product_course_update_body(course_id)
    async with client_for(auth) as client:
        response = await client.patch(
            f"/v1/me/sellers/{SELLER_ID}/products/{product_id}/metadata", content=body
        )
    assert response.status_code == 200
    assert response.json() == {
        "product_id": str(product_id),
        "course_id": str(course_id),
        "product_version": 2,
        "course_version": 2,
        "updated": True,
    }
    assert auth.capabilities == ["catalog.draft.update"]
    statement, parameters = auth.session.calls[0]
    assert "app_private.update_product_course_draft" in statement
    assert parameters["product_id"] == product_id
    assert parameters["course_id"] == course_id
    assert parameters["body_sha256"] == hashlib.sha256(body).hexdigest()


@pytest.mark.asyncio
async def test_patch_product_course_draft_rejects_hidden_offer_fields() -> None:
    product_id, course_id = uuid4(), uuid4()
    auth = FakeAuth(seller_id=SELLER_ID, result=FakeResult(one={}))
    body = product_course_update_body(course_id, offer_title="Oferta escondida")
    async with client_for(auth) as client:
        response = await client.patch(
            f"/v1/me/sellers/{SELLER_ID}/products/{product_id}/metadata", content=body
        )
    assert response.status_code == 422
    assert auth.domain_sessions == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("expected_status", "target_status", "expected_version", "result_version"),
    [
        ("draft", "active", 1, 2),
        ("active", "archived", 2, 3),
        ("active", "draft", 2, 3),
    ],
)
async def test_offer_status_transition_binds_valid_transition_version_and_actor(
    expected_status: str,
    target_status: str,
    expected_version: int,
    result_version: int,
) -> None:
    product_id, offer_id = uuid4(), uuid4()
    auth = FakeAuth(
        seller_id=SELLER_ID,
        result=FakeResult(
            one={
                "offer_status": target_status,
                "offer_version": result_version,
                "changed": True,
            }
        ),
    )
    body = offer_status_body(
        expected_status=expected_status,
        status=target_status,
        expected_offer_version=expected_version,
    )
    async with client_for(auth) as client:
        response = await client.patch(
            f"/v1/me/sellers/{SELLER_ID}/products/{product_id}/offers/{offer_id}/status",
            content=body,
        )
    assert response.status_code == 200
    assert response.json() == {
        "offer_id": str(offer_id),
        "status": target_status,
        "offer_version": result_version,
        "changed": True,
    }
    assert auth.capabilities == ["catalog.offer.status.update"]
    statement, parameters = auth.session.calls[0]
    assert "app_private.transition_offer_status" in statement
    assert parameters["product_id"] == product_id
    assert parameters["offer_id"] == offer_id
    assert parameters["expected_offer_status"] == expected_status
    assert parameters["offer_status"] == target_status
    assert parameters["expected_offer_version"] == expected_version
    assert parameters["body_sha256"] == hashlib.sha256(body).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("expected_status", "target_status"),
    [
        ("draft", "draft"),
        ("draft", "archived"),
        ("active", "active"),
        ("archived", "active"),
        ("archived", "draft"),
        ("archived", "archived"),
        ("draft", "paused"),
    ],
)
async def test_offer_status_transition_rejects_invalid_transition(
    expected_status: str, target_status: str
) -> None:
    auth = FakeAuth(seller_id=SELLER_ID, result=FakeResult())
    async with client_for(auth) as client:
        response = await client.patch(
            f"/v1/me/sellers/{SELLER_ID}/products/{uuid4()}/offers/{uuid4()}/status",
            content=offer_status_body(
                expected_status=expected_status,
                status=target_status,
            ),
        )
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request_body"}
    assert auth.session.calls == []


@pytest.mark.asyncio
async def test_patch_without_pricing_forwards_null_to_atomic_function() -> None:
    product_id, course_id, offer_id = uuid4(), uuid4(), uuid4()
    auth = FakeAuth(
        seller_id=SELLER_ID,
        result=FakeResult(
            one={
                "offer_id": offer_id,
                "product_version": 2,
                "course_version": 2,
                "offer_version": 2,
                "created": False,
            }
        ),
    )
    value = {
        "idempotency_key": str(uuid4()),
        "course_id": str(course_id),
        "offer_id": str(offer_id),
        "slug": "produto-editado",
        "title": "Produto Editado",
        "description": "Rascunho persistido",
        "course_title": "Curso Editado",
        "offer_title": "Oferta Principal",
        "offer_summary": "Condições informativas sem billing",
        "expected_product_version": 1,
        "expected_course_version": 1,
        "expected_offer_version": 1,
    }
    body = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()
    async with client_for(auth) as client:
        response = await client.patch(
            f"/v1/me/sellers/{SELLER_ID}/products/{product_id}", content=body
        )
    assert response.status_code == 200
    # Omitted price is forwarded as NULL so the atomic function preserves the stored
    # value on update (and defaults on create) instead of overwriting it with 0.
    _, parameters = auth.session.calls[0]
    assert parameters["offer_unit_amount_minor"] is None
    assert parameters["offer_currency"] is None
    assert parameters["offer_billing_period"] is None


@pytest.mark.asyncio
async def test_patch_response_echoes_server_validated_pricing() -> None:
    product_id, course_id, offer_id = uuid4(), uuid4(), uuid4()
    auth = FakeAuth(
        seller_id=SELLER_ID,
        result=FakeResult(
            one={
                "offer_id": offer_id,
                "product_version": 2,
                "course_version": 2,
                "offer_version": 1,
                "created": True,
            }
        ),
    )
    body = update_body(
        course_id,
        offer_id,
        offer_unit_amount_minor=39900,
        offer_currency="USD",
        offer_billing_period="monthly",
    )
    async with client_for(auth) as client:
        response = await client.patch(
            f"/v1/me/sellers/{SELLER_ID}/products/{product_id}", content=body
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["offer_unit_amount_minor"] == 39900
    assert payload["offer_currency"] == "USD"
    assert payload["offer_billing_period"] == "monthly"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        update_body(uuid4(), uuid4(), offer_unit_amount_minor=-1),
        update_body(uuid4(), uuid4(), offer_unit_amount_minor=100_000_000_000),
        update_body(uuid4(), uuid4(), offer_unit_amount_minor="19900"),
        update_body(uuid4(), uuid4(), offer_currency="brl"),
        update_body(uuid4(), uuid4(), offer_currency="BRLL"),
        update_body(uuid4(), uuid4(), offer_currency="BR"),
        update_body(uuid4(), uuid4(), offer_currency=990),
        update_body(uuid4(), uuid4(), offer_billing_period="weekly"),
        update_body(uuid4(), uuid4(), offer_billing_period="one-time"),
    ],
)
async def test_invalid_offer_pricing_body_is_rejected(body: bytes) -> None:
    auth = FakeAuth(seller_id=SELLER_ID, result=FakeResult())
    async with client_for(auth) as client:
        response = await client.patch(
            f"/v1/me/sellers/{SELLER_ID}/products/{uuid4()}", content=body
        )
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request_body"}
    assert auth.session.calls == []


def test_detail_offer_projection_includes_public_pricing() -> None:
    now = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
    product_id, course_id, offer_id = uuid4(), uuid4(), uuid4()
    row = {
        "seller_organization_id": SELLER_ID,
        "id": product_id,
        "slug": "produto",
        "title": "Produto",
        "description": "Privado",
        "status": "draft",
        "version": 2,
        "created_at": now,
        "updated_at": now,
        "published_snapshot_id": None,
        "cover_object_key": None,
        "cover_storage_version_id": None,
        "cover_media_type": None,
        "cover_width": None,
        "cover_height": None,
        "course_id": course_id,
        "course_title": "Curso",
        "course_description": "",
        "course_status": "draft",
        "course_draft_version": 2,
        "course_position": 0,
        "curriculum_modules": [],
        "offer_id": offer_id,
        "offer_title": "Oferta",
        "offer_summary": "Informativa",
        "offer_status": "draft",
        "offer_version": 1,
        "offer_unit_amount_minor": 49900,
        "offer_currency": "BRL",
        "offer_billing_period": "yearly",
        "publication_version": None,
        "publication_source_sha256": None,
        "published_at": None,
        "publication_snapshot": None,
    }
    offer = _product_detail_response([row], None)["product"]["offer"]
    assert offer["unit_amount_minor"] == 49900
    assert offer["currency"] == "BRL"
    assert offer["billing_period"] == "yearly"


def test_readiness_blocks_a_semantically_identical_publication() -> None:
    now = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
    seller_id, product_id, course_id, offer_id, publication_id = (
        SELLER_ID,
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    row = {
        "seller_organization_id": seller_id,
        "id": product_id,
        "slug": "produto-publicado",
        "title": "Produto Publicado",
        "description": "Estável",
        "status": "published",
        "version": 3,
        "created_at": now,
        "updated_at": now,
        "published_snapshot_id": publication_id,
        "cover_object_key": None,
        "cover_storage_version_id": None,
        "cover_media_type": None,
        "cover_width": None,
        "cover_height": None,
        "course_id": course_id,
        "course_title": "Curso",
        "course_description": "",
        "course_status": "draft",
        "course_draft_version": 2,
        "course_position": 0,
        "offer_id": offer_id,
        "offer_title": "Oferta",
        "offer_summary": "Informativa",
        "offer_status": "draft",
        "offer_version": 1,
        "offer_unit_amount_minor": 19900,
        "offer_currency": "BRL",
        "offer_billing_period": "one_time",
        "publication_version": 1,
        "publication_source_sha256": "a" * 64,
        "published_at": now,
    }
    row["publication_snapshot"] = {
        "schema_version": 1,
        "seller_organization_id": str(seller_id),
        "product": {
            "id": str(product_id),
            "slug": "produto-publicado",
            "title": "Produto Publicado",
            "description": "Estável",
        },
        "offer": {
            "id": str(offer_id),
            "title": "Oferta",
            "summary": "Informativa",
            "billing_authority": "external_unconfigured",
        },
        "courses": [{"id": str(course_id), "title": "Curso", "description": "", "position": 0}],
    }
    product = _product_detail_response([row], None)["product"]
    assert product["has_unpublished_changes"] is True
    assert product["readiness"] == {
        "can_publish": True,
        "items": [
            {
                "key": "server_validation_complete",
                "label": "Validação do servidor concluída.",
                "ready": True,
            }
        ],
    }


@pytest.mark.asyncio
async def test_publish_binds_complete_expected_version_set_and_is_idempotent() -> None:
    product_id, course_id, offer_id, publication_id = uuid4(), uuid4(), uuid4(), uuid4()
    auth = FakeAuth(
        seller_id=SELLER_ID,
        result=FakeResult(
            one={
                "publication_id": publication_id,
                "publication_version": 1,
                "product_version": 3,
                "source_sha256": "a" * 64,
                "offer_status": "draft",
                "offer_version": 2,
                "created": True,
            }
        ),
    )
    body = publication_body(course_id, offer_id)
    async with client_for(auth) as client:
        response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/products/{product_id}/publications",
            content=body,
        )
    assert response.status_code == 201
    assert response.json()["publication_id"] == str(publication_id)
    assert auth.capabilities == ["catalog.publication.create"]
    statement, parameters = auth.session.calls[0]
    assert "app_private.publish_product_with_access" in statement
    # A caller written before the access mode existed must keep publishing free
    # access, never activate a sale as a side effect of the upgrade.
    assert parameters["access_mode"] == "free"
    assert json.loads(parameters["expected_course_versions"]) == {str(course_id): 2}
    assert parameters["body_sha256"] == hashlib.sha256(body).hexdigest()


@pytest.mark.asyncio
async def test_publish_rejects_duplicate_or_incomplete_course_version_sets_before_database() -> (
    None
):
    product_id, course_id, offer_id = uuid4(), uuid4(), uuid4()
    auth = FakeAuth(seller_id=SELLER_ID, result=FakeResult())
    duplicate = publication_body(
        course_id,
        offer_id,
        course_versions=[
            {"id": str(course_id), "version": 2},
            {"id": str(course_id), "version": 3},
        ],
    )
    incomplete = publication_body(course_id, offer_id, course_versions=[])
    async with client_for(auth) as client:
        duplicate_response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/products/{product_id}/publications",
            content=duplicate,
        )
        incomplete_response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/products/{product_id}/publications",
            content=incomplete,
        )
    assert duplicate_response.status_code == 422
    assert incomplete_response.status_code == 422
    assert auth.session.calls == []


@pytest.mark.asyncio
async def test_curriculum_read_and_create_bind_the_full_tenant_path() -> None:
    product_id, course_id, module_id = uuid4(), uuid4(), uuid4()
    read_auth = FakeAuth(
        seller_id=SELLER_ID,
        result=FakeResult(
            one={
                "course_version": 3,
                "curriculum": [],
                "module_total": 0,
                "lesson_total": 0,
                "lesson_totals_by_module": {},
            }
        ),
    )
    async with client_for(read_auth) as client:
        response = await client.get(
            f"/v1/me/sellers/{SELLER_ID}/products/{product_id}/courses/{course_id}/curriculum"
        )
    assert response.status_code == 200
    assert response.json()["course"] == {"id": str(course_id), "draft_version": 3}
    assert read_auth.capabilities == ["catalog.curriculum.read"]
    statement, parameters = read_auth.session.calls[0]
    assert "app_private.read_course_curriculum" in statement
    assert parameters["seller_id"] == SELLER_ID
    assert parameters["product_id"] == product_id
    assert parameters["course_id"] == course_id

    create_auth = FakeAuth(
        seller_id=SELLER_ID,
        result=FakeResult(one={"module_id": module_id, "course_version": 4, "created": True}),
    )
    body = curriculum_body(title="Módulo inicial", expected_course_version=3)
    async with client_for(create_auth) as client:
        response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/products/{product_id}/courses/{course_id}/modules",
            content=body,
        )
    assert response.status_code == 201
    assert response.json() == {
        "module_id": str(module_id),
        "course_version": 4,
        "created": True,
    }
    assert create_auth.capabilities == ["catalog.curriculum.create"]
    statement, parameters = create_auth.session.calls[0]
    assert "app_private.create_course_module" in statement
    assert parameters["body_sha256"] == hashlib.sha256(body).hexdigest()
    assert parameters["expected_course_version"] == 3


@pytest.mark.asyncio
async def test_curriculum_rejects_cross_tenant_invalid_content_and_ambiguous_order() -> None:
    product_id, course_id, module_id = uuid4(), uuid4(), uuid4()
    cross_tenant = FakeAuth(seller_id=OTHER_SELLER_ID, result=FakeResult())
    async with client_for(cross_tenant) as client:
        response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/products/{product_id}/courses/{course_id}/modules",
            content=curriculum_body(title="Módulo"),
        )
    assert response.status_code == 401
    assert cross_tenant.session.calls == []

    invalid = FakeAuth(seller_id=SELLER_ID, result=FakeResult())
    async with client_for(invalid) as client:
        content = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/products/{product_id}/courses/{course_id}/modules/{module_id}/lessons",
            content=curriculum_body(title="Aula", kind="html", content="<script>x</script>"),
        )
        duplicate = await client.put(
            f"/v1/me/sellers/{SELLER_ID}/products/{product_id}/courses/{course_id}/modules/order",
            content=curriculum_body(ordered_ids=[str(module_id), str(module_id)]),
        )
    assert content.status_code == 422
    assert duplicate.status_code == 422
    assert invalid.session.calls == []


@pytest.mark.asyncio
@pytest.mark.skipif(
    not all((RUNTIME_URL, CONTEXT_KEY_HEX)),
    reason="isolated PostgreSQL environment is required",
)
async def test_signed_api_creates_replays_and_lists_persistent_draft() -> None:
    assert RUNTIME_URL and CONTEXT_KEY_HEX
    internal_key = bytes(range(32, 64))
    service_credential = bytes(range(96, 128))
    key_id = "catalog-api-v1"
    issuer = "appsell-catalog-bff-test"
    audience = "appsell-catalog-api-test"
    identity_issuer = "https://identity.catalog-api.test.autonomia.site"
    subject = f"catalog-api-user-{uuid4()}"
    identity_id = uuid5(NAMESPACE_URL, f"{identity_issuer}\0{subject}")
    session_id = f"catalog-api-session-{uuid4()}"
    environment = {
        "APPSELL_API_ENVIRONMENT": "ci",
        "APPSELL_API_DATABASE_URL": RUNTIME_URL.replace(
            "postgresql://", "postgresql+asyncpg://", 1
        ),
        "APPSELL_API_INTERNAL_ISSUER": issuer,
        "APPSELL_API_INTERNAL_AUDIENCE": audience,
        "APPSELL_API_IDENTITY_ISSUER": identity_issuer,
        "APPSELL_API_INTERNAL_HMAC_KEYRING_B64_JSON": json.dumps(
            [
                {
                    "id": key_id,
                    "key": base64.b64encode(internal_key).decode(),
                    "active": True,
                }
            ]
        ),
        "APPSELL_API_CONTEXT_HMAC_KEY_B64": base64.b64encode(
            bytes.fromhex(CONTEXT_KEY_HEX)
        ).decode(),
        "APPSELL_API_SERVICE_CREDENTIAL_B64": base64.b64encode(service_credential).decode(),
    }
    previous = {key: os.environ.get(key) for key in environment}
    os.environ.update(environment)
    get_settings.cache_clear()

    def headers(
        *,
        method: str,
        path: str,
        capability: str,
        body: bytes = b"",
        seller_id: UUID | None = None,
    ) -> dict[str, str]:
        now = int(time.time())
        payload = {
            "version": 1,
            "kid": key_id,
            "issuer": issuer,
            "audience": audience,
            "session_ref": hashlib.sha256(
                b"appsell.session-ref.v1\n" + session_id.encode()
            ).hexdigest(),
            "session_epoch": 1,
            "identity": {
                "issuer": identity_issuer,
                "sub": subject,
                "user_id": str(identity_id),
            },
            "capability": capability,
            "seller_organization_id": str(seller_id) if seller_id else None,
            "method": method,
            "path": path,
            "query_sha256": hashlib.sha256(b"").hexdigest(),
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "request_id": str(uuid4()),
            "iat": now,
            "exp": now + 30,
        }
        encoded = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode()
        ).rstrip(b"=")
        signature = base64.urlsafe_b64encode(
            hmac.digest(internal_key, b"appsell.internal.v1." + encoded, "sha256")
        ).rstrip(b"=")
        return {
            "x-appsell-service-credential": base64.b64encode(service_credential).decode(),
            "x-appsell-internal-envelope": f"v1.{encoded.decode()}.{signature.decode()}",
            "content-type": "application/json",
        }

    try:
        async with domain_app.router.lifespan_context(domain_app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=domain_app), base_url="http://api.test"
            ) as client:
                sync_body = json.dumps(
                    {"email": f"catalog-{uuid4().hex}@example.test", "email_verified": True},
                    separators=(",", ":"),
                ).encode()
                synced = await client.put(
                    "/v1/me",
                    content=sync_body,
                    headers=headers(
                        method="PUT",
                        path="/v1/me",
                        capability="identity.sync",
                        body=sync_body,
                    ),
                )
                assert synced.status_code == 200

                seller_body = json.dumps(
                    {
                        "idempotency_key": str(uuid4()),
                        "slug": f"catalog-api-{uuid4().hex[:12]}",
                        "display_name": "Catalog API Test",
                    },
                    separators=(",", ":"),
                ).encode()
                onboarded = await client.post(
                    "/v1/me/sellers",
                    content=seller_body,
                    headers=headers(
                        method="POST",
                        path="/v1/me/sellers",
                        capability="seller.onboard",
                        body=seller_body,
                    ),
                )
                assert onboarded.status_code == 201
                seller_id = UUID(onboarded.json()["id"])
                path = f"/v1/me/sellers/{seller_id}/products"
                product_body = draft_body(slug=f"produto-{uuid4().hex[:12]}")
                idempotency_key = json.loads(product_body)["idempotency_key"]

                created = await client.post(
                    path,
                    content=product_body,
                    headers=headers(
                        method="POST",
                        path=path,
                        capability="catalog.draft.create",
                        body=product_body,
                        seller_id=seller_id,
                    ),
                )
                assert created.status_code == 201
                assert created.json()["status"] == "draft"

                replay_body = json.loads(product_body)
                replay_body["idempotency_key"] = idempotency_key
                replay_bytes = json.dumps(replay_body, separators=(",", ":")).encode()
                replayed = await client.post(
                    path,
                    content=replay_bytes,
                    headers=headers(
                        method="POST",
                        path=path,
                        capability="catalog.draft.create",
                        body=replay_bytes,
                        seller_id=seller_id,
                    ),
                )
                assert replayed.status_code == 200
                assert replayed.json() == {**created.json(), "created": False}

                listed = await client.get(
                    path,
                    headers=headers(
                        method="GET",
                        path=path,
                        capability="catalog.draft.read",
                        seller_id=seller_id,
                    ),
                )
                assert listed.status_code == 200
                assert listed.json()["products"][0]["id"] == created.json()["product_id"]
                assert listed.json()["products"][0]["status"] == "draft"
                assert listed.json()["products"][0]["course"]["status"] == "draft"

                detail_path = f"{path}/{created.json()['product_id']}"
                detail = await client.get(
                    detail_path,
                    headers=headers(
                        method="GET",
                        path=detail_path,
                        capability="catalog.draft.read",
                        seller_id=seller_id,
                    ),
                )
                assert detail.status_code == 200, detail.text
                assert detail.json()["product"]["id"] == created.json()["product_id"]
                assert detail.json()["product"]["seller_slug"] == json.loads(seller_body)["slug"]
    finally:
        get_settings.cache_clear()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


LINK_PREFIX = "7f3a92aa"
ROTATED_PREFIX = "aa11bb22"


def access_link_body(**overrides: Any) -> bytes:
    value: dict[str, Any] = {
        "idempotency_key": str(uuid4()),
        "label": "Turma Royalty Seguros",
        "token_sha256": "b" * 64,
        "token_prefix": LINK_PREFIX,
        "max_uses": 30,
        **overrides,
    }
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()


@pytest.mark.asyncio
async def test_publish_forwards_paid_access_mode_to_the_atomic_function() -> None:
    # Publication and sale activation must reach the domain as ONE call. If the
    # route ever split them again, the product would be publicly free between the
    # two writes.
    product_id, course_id, offer_id, publication_id = uuid4(), uuid4(), uuid4(), uuid4()
    auth = FakeAuth(
        seller_id=SELLER_ID,
        result=FakeResult(
            one={
                "publication_id": publication_id,
                "publication_version": 1,
                "product_version": 3,
                "source_sha256": "a" * 64,
                "offer_status": "active",
                "offer_version": 3,
                "created": True,
            }
        ),
    )
    body = publication_body(course_id, offer_id, access_mode="paid")
    async with client_for(auth) as client:
        response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/products/{product_id}/publications",
            content=body,
        )
    assert response.status_code == 201
    payload = response.json()
    assert payload["access_mode"] == "paid"
    assert payload["offer_status"] == "active"
    assert payload["offer_version"] == 3
    assert auth.domain_sessions == 1
    statement, parameters = auth.session.calls[0]
    assert "app_private.publish_product_with_access" in statement
    assert parameters["access_mode"] == "paid"


@pytest.mark.asyncio
async def test_publish_rejects_an_unknown_access_mode_before_the_database() -> None:
    product_id, course_id, offer_id = uuid4(), uuid4(), uuid4()
    auth = FakeAuth(seller_id=SELLER_ID, result=FakeResult(one={}))
    async with client_for(auth) as client:
        response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/products/{product_id}/publications",
            content=publication_body(course_id, offer_id, access_mode="gratis"),
        )
    assert response.status_code == 422
    assert auth.domain_sessions == 0


def test_access_domain_errors_map_to_distinct_actionable_reasons() -> None:
    # A seller who cannot sell yet has to be told why. Collapsing these into one
    # generic 422 is what leaves a dead button on screen with no explanation.
    # The unavailable case deliberately answers 404 for revoked, expired and
    # unknown links alike, so probing cannot reveal whether a token exists.
    for sqlstate, expected_status, expected_detail in [
        ("P4223", 422, "payout_account_required"),
        ("P4224", 409, "access_link_exhausted"),
        ("P4225", 404, "access_link_unavailable"),
    ]:
        mapped = _domain_error(
            SimpleNamespace(  # type: ignore[arg-type]
                orig=SimpleNamespace(sqlstate=sqlstate, constraint_name=None)
            )
        )
        assert mapped.status_code == expected_status
        assert mapped.detail == expected_detail


@pytest.mark.asyncio
async def test_create_access_link_sends_only_the_digest_and_prefix() -> None:
    # The raw token must never reach this service. If it ever did, it would land
    # in request logs and in the database, and a dump would hand out access.
    product_id, link_id = uuid4(), uuid4()
    auth = FakeAuth(
        seller_id=SELLER_ID,
        result=FakeResult(
            one={
                "access_link_id": link_id,
                "token_prefix": LINK_PREFIX,
                "label": "Turma Royalty Seguros",
                "max_uses": 30,
                "redeemed_count": 0,
                "expires_at": None,
                "created_at": None,
            }
        ),
    )
    body = access_link_body()
    async with client_for(auth) as client:
        response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/products/{product_id}/access-links",
            content=body,
        )
    assert response.status_code == 201
    payload = response.json()
    assert payload["access_link_id"] == str(link_id)
    assert payload["token_prefix"] == LINK_PREFIX
    assert "token" not in payload
    assert "token_sha256" not in payload
    assert auth.capabilities == ["catalog.access_link.create"]
    statement, parameters = auth.session.calls[0]
    assert "app_private.create_product_access_link" in statement
    assert parameters["token_sha256"] == "b" * 64
    assert parameters["max_uses"] == 30


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"token_sha256": "z" * 64},
        {"token_sha256": "b" * 63},
        {"token_prefix": "7f3a"},
        {"label": " espaco na ponta"},
        {"label": ""},
        {"max_uses": 0},
        {"max_uses": 100_001},
        {"expires_at": "not-a-date"},
    ],
)
async def test_create_access_link_rejects_malformed_input_before_the_database(
    overrides: dict[str, Any],
) -> None:
    product_id = uuid4()
    auth = FakeAuth(seller_id=SELLER_ID, result=FakeResult(one={}))
    async with client_for(auth) as client:
        response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/products/{product_id}/access-links",
            content=access_link_body(**overrides),
        )
    assert response.status_code == 422
    assert auth.domain_sessions == 0


@pytest.mark.asyncio
async def test_list_access_links_never_returns_a_token() -> None:
    product_id, link_id = uuid4(), uuid4()
    auth = FakeAuth(
        seller_id=SELLER_ID,
        result=FakeResult(
            all=[
                {
                    "access_link_id": link_id,
                    "token_prefix": LINK_PREFIX,
                    "label": "Turma Royalty Seguros",
                    "max_uses": 30,
                    "redeemed_count": 12,
                    "expires_at": None,
                    "revoked_at": None,
                    "rotated_at": None,
                    "created_at": None,
                }
            ]
        ),
    )
    async with client_for(auth) as client:
        response = await client.get(
            f"/v1/me/sellers/{SELLER_ID}/products/{product_id}/access-links"
        )
    assert response.status_code == 200
    links = response.json()["access_links"]
    assert len(links) == 1
    assert links[0]["redeemed_count"] == 12
    assert not any(key.startswith("token_sha") for key in links[0])
    assert auth.capabilities == ["catalog.access_link.read"]


@pytest.mark.asyncio
async def test_revoke_access_link_is_idempotent_and_reports_whether_it_changed() -> None:
    product_id, link_id = uuid4(), uuid4()
    auth = FakeAuth(
        seller_id=SELLER_ID,
        result=FakeResult(one={"access_link_id": link_id, "revoked_at": None, "changed": False}),
    )
    async with client_for(auth) as client:
        response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/products/{product_id}/access-links/{link_id}/revoke",
            content=json.dumps({"idempotency_key": str(uuid4())}, separators=(",", ":")).encode(),
        )
    assert response.status_code == 200
    assert response.json()["changed"] is False
    assert auth.capabilities == ["catalog.access_link.update"]
    statement, _ = auth.session.calls[0]
    assert "app_private.revoke_product_access_link" in statement


@pytest.mark.asyncio
async def test_rotate_access_link_keeps_the_seat_budget() -> None:
    # Losing the link must not cost the seats already handed out, otherwise a
    # rotation silently re-opens a closed class.
    product_id, link_id = uuid4(), uuid4()
    auth = FakeAuth(
        seller_id=SELLER_ID,
        result=FakeResult(
            one={
                "access_link_id": link_id,
                "token_prefix": ROTATED_PREFIX,
                "redeemed_count": 12,
                "max_uses": 30,
                "rotated_at": None,
            }
        ),
    )
    body = json.dumps(
        {
            "idempotency_key": str(uuid4()),
            "token_sha256": "c" * 64,
            "token_prefix": ROTATED_PREFIX,
        },
        separators=(",", ":"),
    ).encode()
    async with client_for(auth) as client:
        response = await client.post(
            f"/v1/me/sellers/{SELLER_ID}/products/{product_id}/access-links/{link_id}/rotate",
            content=body,
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["redeemed_count"] == 12
    assert payload["max_uses"] == 30
    assert payload["token_prefix"] == ROTATED_PREFIX
    statement, parameters = auth.session.calls[0]
    assert "app_private.rotate_product_access_link" in statement
    assert parameters["token_sha256"] == "c" * 64


def material_body(**overrides: Any) -> bytes:
    value: dict[str, Any] = {
        "idempotency_key": str(uuid4()),
        "expected_course_version": 2,
        "kind": "external",
        "title": "Circular oficial",
        "url": "https://www.gov.br/susep/circular-621",
        **overrides,
    }
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()


def material_path(product_id: UUID, course_id: UUID, module_id: UUID, lesson_id: UUID) -> str:
    return (
        f"/v1/me/sellers/{SELLER_ID}/products/{product_id}/courses/{course_id}"
        f"/modules/{module_id}/lessons/{lesson_id}/materials"
    )


@pytest.mark.asyncio
async def test_an_external_material_is_stored_and_never_fetched() -> None:
    # The server keeps the address and hands it to the student. It must not visit
    # it, because that would turn a seller supplied string into a server request.
    product_id, course_id, module_id, lesson_id = uuid4(), uuid4(), uuid4(), uuid4()
    material_id = uuid4()
    auth = FakeAuth(
        seller_id=SELLER_ID,
        result=FakeResult(one={"material_id": material_id, "course_version": 3, "position": 0}),
    )
    async with client_for(auth) as client:
        response = await client.post(
            material_path(product_id, course_id, module_id, lesson_id),
            content=material_body(),
        )
    assert response.status_code == 201
    assert response.json()["material_id"] == str(material_id)
    assert auth.capabilities == ["catalog.curriculum.create"]
    statement, parameters = auth.session.calls[0]
    assert "app_private.create_lesson_material_link" in statement
    assert parameters["url"] == "https://www.gov.br/susep/circular-621"
    assert parameters["provider"] is None
    assert parameters["video_id"] is None


@pytest.mark.asyncio
async def test_an_embedded_video_is_a_provider_and_an_id_never_markup() -> None:
    product_id, course_id, module_id, lesson_id = uuid4(), uuid4(), uuid4(), uuid4()
    auth = FakeAuth(
        seller_id=SELLER_ID,
        result=FakeResult(one={"material_id": uuid4(), "course_version": 3, "position": 1}),
    )
    async with client_for(auth) as client:
        response = await client.post(
            material_path(product_id, course_id, module_id, lesson_id),
            content=material_body(
                kind="embed", url=None, provider="youtube", video_id="dQw4w9WgXcQ"
            ),
        )
    assert response.status_code == 201
    _, parameters = auth.session.calls[0]
    assert parameters["provider"] == "youtube"
    assert parameters["video_id"] == "dQw4w9WgXcQ"
    assert parameters["url"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"url": "http://inseguro.test/pagina"},
        {"url": "javascript:alert(1)"},
        {"url": "https://host/<script>"},
        {"url": 'https://host/"onload'},
        {"kind": "embed", "url": None, "provider": "tiktok", "video_id": "abc"},
        {"kind": "embed", "url": None, "provider": "youtube", "video_id": "<iframe>"},
        {"kind": "embed", "url": None, "provider": "youtube", "video_id": "a" * 65},
        {"kind": "embed", "provider": "youtube", "video_id": "abc"},
        {"kind": "external", "provider": "youtube"},
        {"title": " espaco na ponta"},
        {"title": "x"},
    ],
)
async def test_a_material_is_refused_before_the_database(overrides: dict[str, Any]) -> None:
    product_id, course_id, module_id, lesson_id = uuid4(), uuid4(), uuid4(), uuid4()
    auth = FakeAuth(seller_id=SELLER_ID, result=FakeResult(one={}))
    async with client_for(auth) as client:
        response = await client.post(
            material_path(product_id, course_id, module_id, lesson_id),
            content=material_body(**overrides),
        )
    assert response.status_code == 422
    assert auth.domain_sessions == 0
