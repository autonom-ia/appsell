"""As rotas da Minha conta (#351), pela porta da API.

A suíte de Postgres prova as funções; esta prova a borda: o corpo vazio é
obrigatório, o sujeito vem do envelope, e o seller no contexto fecha a porta.
"""

from __future__ import annotations

import json

from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy.exc import DBAPIError
from test_api_onboarding import client, signed_headers  # noqa: F401 (fixture)

from appsell_api.routes import _export_error

pytestmark = pytest.mark.asyncio

EMPTY = b"{}"


async def _sincroniza(api: httpx.AsyncClient) -> None:
    body = json.dumps(
        {"email": "creator@example.test", "email_verified": True}, separators=(",", ":")
    ).encode()
    response = await api.put(
        "/v1/me",
        content=body,
        headers=signed_headers(method="PUT", path="/v1/me", capability="identity.sync", body=body),
    )
    assert response.status_code == 200, response.text


async def test_o_status_a_exportacao_e_o_pedido_seguem_o_envelope(
    client: httpx.AsyncClient,  # noqa: F811
) -> None:
    await _sincroniza(client)

    status = await client.get(
        "/v1/me/account",
        headers=signed_headers(method="GET", path="/v1/me/account", capability="account.read"),
    )
    assert status.status_code == 200, status.text
    assert status.json() == {"deletion_requested_at": None}

    exported = await client.post(
        "/v1/me/account/export",
        content=EMPTY,
        headers=signed_headers(
            method="POST", path="/v1/me/account/export", capability="account.export", body=EMPTY
        ),
    )
    assert exported.status_code == 200, exported.text
    documento = exported.json()
    assert documento["schema_version"] == 1
    assert documento["identity"]["email"] == "creator@example.test"
    assert "identity_sub" not in exported.text and "sha256" not in exported.text

    primeiro = await client.post(
        "/v1/me/account/deletion-request",
        content=EMPTY,
        headers=signed_headers(
            method="POST",
            path="/v1/me/account/deletion-request",
            capability="account.deletion.request",
            body=EMPTY,
        ),
    )
    assert primeiro.status_code == 201, primeiro.text
    assert primeiro.json()["already_requested"] is False
    quando = primeiro.json()["requested_at"]

    segundo = await client.post(
        "/v1/me/account/deletion-request",
        content=EMPTY,
        headers=signed_headers(
            method="POST",
            path="/v1/me/account/deletion-request",
            capability="account.deletion.request",
            body=EMPTY,
        ),
    )
    assert segundo.status_code == 200, segundo.text
    assert segundo.json() == {"requested_at": quando, "already_requested": True}

    depois = await client.get(
        "/v1/me/account",
        headers=signed_headers(method="GET", path="/v1/me/account", capability="account.read"),
    )
    assert depois.json() == {"deletion_requested_at": quando}


async def test_a_exportacao_recusa_corpo_com_campo_seller_e_capability_errada(
    client: httpx.AsyncClient,  # noqa: F811
) -> None:
    await _sincroniza(client)

    # Um id de usuário no corpo não é ignorado: é pedido malformado.
    forjado = json.dumps({"identity_user_id": "00000000-0000-4000-8000-000000000001"}).encode()
    com_campo = await client.post(
        "/v1/me/account/export",
        content=forjado,
        headers=signed_headers(
            method="POST", path="/v1/me/account/export", capability="account.export", body=forjado
        ),
    )
    assert com_campo.status_code == 422, com_campo.text

    # A conta é da pessoa, nunca de uma loja: seller no envelope fecha a porta.
    com_loja = await client.post(
        "/v1/me/account/export",
        content=EMPTY,
        headers=signed_headers(
            method="POST",
            path="/v1/me/account/export",
            capability="account.export",
            body=EMPTY,
            seller_id="00000000-0000-4000-8000-000000000002",
        ),
    )
    assert com_loja.status_code == 401, com_loja.text

    # A capability é da rota, não do portador.
    outra = await client.post(
        "/v1/me/account/export",
        content=EMPTY,
        headers=signed_headers(
            method="POST", path="/v1/me/account/export", capability="library.read", body=EMPTY
        ),
    )
    assert outra.status_code == 401, outra.text


def test_o_teto_do_banco_vira_413_e_o_resto_segue_o_mapa_comum() -> None:
    # 54000 é "grande demais", não "fora do ar": a tela precisa distinguir.
    def erro(sqlstate: str) -> DBAPIError:
        return DBAPIError("select 1", {}, SimpleNamespace(sqlstate=sqlstate))

    grande = _export_error(erro("54000"))
    assert isinstance(grande, HTTPException)
    assert (grande.status_code, grande.detail) == (413, "account_export_too_large")
    negado = _export_error(erro("42501"))
    assert (negado.status_code, negado.detail) == (403, "forbidden")
    caiu = _export_error(erro("08006"))
    assert (caiu.status_code, caiu.detail) == (503, "domain_unavailable")
