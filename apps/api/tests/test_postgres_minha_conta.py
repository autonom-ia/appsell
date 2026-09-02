"""Minha conta do comprador (#351): a exportação é só do sujeito, e o pedido
de exclusão é um registro — não uma exclusão.

O risco desta onda é vazamento de PII: a exportação é uma superfície nova
que junta dado de nove tabelas. Cada teste abaixo existe para um modo de
falha específico, e os negativos são os que importam.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.errors import InsufficientPrivilege
from psycopg.types.json import Jsonb

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.environ.get("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY_HEX = os.environ.get("APPSELL_TEST_CONTEXT_KEY_HEX")

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY_HEX)),
    reason="isolated PostgreSQL environment is required",
)

ISSUER = "https://identity.minhaconta.test"


def apply_context(
    connection: psycopg.Connection[Any], actor: UUID, seller: UUID | None, capability: str
) -> None:
    issued = int(time.time())
    expires = issued + 60
    payload = f"{actor}:{seller or ''}:{capability}:{issued}:{expires}"
    signature = hmac.new(
        bytes.fromhex(CONTEXT_KEY_HEX or ""), payload.encode(), hashlib.sha256
    ).hexdigest()
    connection.execute(
        "select app_private.apply_request_context(%s,%s,%s,%s,%s,%s)",
        (actor, seller, capability, issued, expires, signature),
    )


class Mundo:
    """Duas lojas, dois compradores, cada um com o seu curso — e nada em comum."""

    def __init__(self) -> None:
        sufixo = uuid4().hex[:10]
        self.sufixo = sufixo
        self.loja_a, self.loja_b = uuid4(), uuid4()
        self.dona_a, self.dona_b = uuid4(), uuid4()
        self.ana, self.bia = uuid4(), uuid4()
        self.produto_a, self.produto_b = uuid4(), uuid4()
        self.publicacao_a, self.publicacao_b = uuid4(), uuid4()
        self.email_ana = f"ana-{sufixo}@example.test"
        self.email_bia = f"bia-{sufixo}@example.test"
        self.titulo_a = f"Curso da Ana {sufixo}"
        self.titulo_b = f"Curso da Bia {sufixo}"

    def semear(self) -> None:
        assert ADMIN_URL
        with psycopg.connect(ADMIN_URL) as connection:
            for user, sub, email, nome in (
                (self.dona_a, f"dona-a-{self.sufixo}", f"dona-a-{self.sufixo}@example.test", None),
                (self.dona_b, f"dona-b-{self.sufixo}", f"dona-b-{self.sufixo}@example.test", None),
                (self.ana, f"ana-{self.sufixo}", self.email_ana, "Ana Teste"),
                (self.bia, f"bia-{self.sufixo}", self.email_bia, "Bia Teste"),
            ):
                connection.execute(
                    "insert into identity_user(id,identity_issuer,identity_sub,email_normalized,"
                    "email_verified,display_name) values(%s,%s,%s,%s,true,%s)",
                    (user, ISSUER, sub, email, nome),
                )
            for loja, dona, produto, publicacao, titulo, rotulo in (
                (self.loja_a, self.dona_a, self.produto_a, self.publicacao_a, self.titulo_a, "a"),
                (self.loja_b, self.dona_b, self.produto_b, self.publicacao_b, self.titulo_b, "b"),
            ):
                connection.execute(
                    "insert into seller_organization(id,slug,display_name,status) "
                    "values(%s,%s,%s,'active')",
                    (loja, f"loja-{rotulo}-{self.sufixo}", f"Loja {rotulo.upper()} {self.sufixo}"),
                )
                connection.execute(
                    "insert into seller_membership(seller_organization_id,identity_user_id,"
                    "role,status) values(%s,%s,'seller_owner','active')",
                    (loja, dona),
                )
                connection.execute(
                    "insert into catalog_product(seller_organization_id,id,slug,title,description)"
                    " values(%s,%s,%s,%s,'')",
                    (loja, produto, f"produto-{rotulo}-{self.sufixo}", titulo),
                )
                connection.execute(
                    "insert into publication(seller_organization_id,id,catalog_product_id,"
                    "version,schema_version,source_sha256,snapshot,actor_identity_user_id) "
                    "values(%s,%s,%s,1,2,repeat('0',64),%s,%s)",
                    (loja, publicacao, produto, Jsonb({}), dona),
                )
            for loja, comprador, produto, publicacao in (
                (self.loja_a, self.ana, self.produto_a, self.publicacao_a),
                (self.loja_b, self.bia, self.produto_b, self.publicacao_b),
            ):
                connection.execute(
                    "insert into buyer_entitlement(seller_organization_id,id,"
                    "buyer_identity_user_id,catalog_product_id,publication_id,reference_key,"
                    "source,status,progress_percent) "
                    "values(%s,%s,%s,%s,%s,'pytest-minha-conta','manual','active',40)",
                    (loja, uuid4(), comprador, produto, publicacao),
                )
            # A Bia tem histórico próprio, com metadata que NÃO pode vazar para
            # a Ana — nem por junção errada, nem por metadata copiada.
            connection.execute(
                "insert into audit_event(seller_organization_id,actor_identity_user_id,action,"
                "target_type,result,request_id,metadata) values(%s,%s,'library.opened',"
                "'buyer_entitlement','succeeded',gen_random_uuid(),%s)",
                (self.loja_b, self.bia, Jsonb({"segredo_da_bia": self.email_bia})),
            )
            connection.commit()


@pytest.fixture(scope="module")
def mundo() -> Mundo:
    mundo = Mundo()
    mundo.semear()
    return mundo


def exportar(
    actor: UUID, alvo: UUID, *, seller: UUID | None = None, capability: str = "account.export"
) -> dict[str, Any]:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, actor, seller, capability)
        row = connection.execute(
            "select app_private.export_account_data(%s,%s)", (alvo, uuid4())
        ).fetchone()
        connection.commit()
    assert row is not None
    return dict(row[0])


def audits(actor: UUID, action: str) -> list[dict[str, Any]]:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        rows = connection.execute(
            "select seller_organization_id,request_id,result,target_type,target_id "
            "from audit_event where actor_identity_user_id=%s and action=%s order by created_at",
            (actor, action),
        ).fetchall()
    return [
        {
            "seller": row[0],
            "request_id": row[1],
            "result": row[2],
            "target_type": row[3],
            "target_id": row[4],
        }
        for row in rows
    ]


def test_a_exportacao_traz_so_o_proprio_sujeito(mundo: Mundo) -> None:
    # Modo de falha: uma junção sem o filtro do sujeito devolveria o curso da
    # Bia junto do da Ana. Duas compradoras, duas lojas, zero interseção.
    exportado = exportar(mundo.ana, mundo.ana)
    assert exportado["schema_version"] == 1
    assert exportado["identity"]["email"] == mundo.email_ana
    assert exportado["identity"]["display_name"] == "Ana Teste"
    assert [item["product"] for item in exportado["accesses"]] == [mundo.titulo_a]
    assert exportado["accesses"][0]["status"] == "active"
    assert exportado["accesses"][0]["progress_percent"] == 40
    assert exportado["memberships"] == []
    despejo = json.dumps(exportado, ensure_ascii=False)
    assert mundo.email_bia not in despejo
    assert mundo.titulo_b not in despejo
    assert "segredo_da_bia" not in despejo


def test_a_exportacao_nao_carrega_segredo_nem_id_interno(mundo: Mundo) -> None:
    # Modo de falha: "select *" numa das tabelas. Os nomes abaixo são os campos
    # que nunca podem sair — asserção textual sobre o despejo inteiro.
    exportado = exportar(mundo.ana, mundo.ana)
    despejo = json.dumps(exportado, ensure_ascii=False)
    for proibido in (
        "identity_sub",
        "identity_issuer",
        "sha256",
        "points",
        "metadata",
        "request_id",
        "target_id",
        str(mundo.ana),
        str(mundo.loja_a),
        str(mundo.produto_a),
        ISSUER,
    ):
        assert proibido not in despejo, proibido


def test_a_exportacao_grava_exatamente_um_audit_com_o_proprio_ator(mundo: Mundo) -> None:
    antes = len(audits(mundo.ana, "account.data.exported"))
    exportar(mundo.ana, mundo.ana)
    registros = audits(mundo.ana, "account.data.exported")
    assert len(registros) == antes + 1
    ultimo = registros[-1]
    assert ultimo["seller"] is None
    assert ultimo["result"] == "succeeded"
    assert ultimo["target_type"] == "identity_user"
    assert ultimo["target_id"] == mundo.ana
    # E a própria exportação aparece na atividade da pessoa da próxima vez.
    acoes = {item["action"] for item in exportar(mundo.ana, mundo.ana)["activity"]}
    assert "account.data.exported" in acoes


@pytest.mark.parametrize(
    ("descricao", "montar"),
    [
        ("pedir os dados de outra pessoa", lambda m: dict(actor=m.ana, alvo=m.bia)),
        (
            "com loja no contexto",
            lambda m: dict(actor=m.ana, alvo=m.ana, seller=m.loja_a),
        ),
        (
            "com a capability da biblioteca",
            lambda m: dict(actor=m.ana, alvo=m.ana, capability="library.read"),
        ),
        (
            "a dona da loja tentando ler a compradora",
            lambda m: dict(actor=m.dona_a, alvo=m.ana),
        ),
    ],
)
def test_a_exportacao_recusa_qualquer_contexto_que_nao_seja_o_proprio(
    mundo: Mundo, descricao: str, montar: Any
) -> None:
    # Modo de falha: a guarda confia no parâmetro em vez do contexto assinado.
    # Cada caso é uma forma de pedir dado que não é seu. Todos: 42501, e
    # nenhum audit de exportação para a vítima.
    antes = len(audits(mundo.bia, "account.data.exported"))
    with pytest.raises(InsufficientPrivilege):
        exportar(**montar(mundo))
    assert len(audits(mundo.bia, "account.data.exported")) == antes, descricao


def test_o_runtime_nao_grava_audit_de_comprador_sem_a_funcao(mundo: Mundo) -> None:
    # A premissa da migration: sem SECURITY DEFINER o comprador puro não
    # escreve auditoria — a política exige membership. Se isto um dia passar,
    # a função deixou de ser necessária e a matriz precisa ser revista.
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, mundo.ana, None, "audit.write")
        with pytest.raises(InsufficientPrivilege):
            connection.execute(
                "insert into audit_event(seller_organization_id,actor_identity_user_id,action,"
                "target_type,result,request_id) values(null,%s,'account.data.exported',"
                "'identity_user','succeeded',gen_random_uuid())",
                (mundo.ana,),
            )


def pedir_exclusao(
    actor: UUID, alvo: UUID, *, capability: str = "account.deletion.request"
) -> tuple[Any, bool]:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, actor, None, capability)
        row = connection.execute(
            "select requested_at,already_requested "
            "from app_private.request_account_deletion(%s,%s)",
            (alvo, uuid4()),
        ).fetchone()
        connection.commit()
    assert row is not None
    return row[0], bool(row[1])


def ler_status(actor: UUID, alvo: UUID, *, capability: str = "account.read") -> Any:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, actor, None, capability)
        row = connection.execute(
            "select deletion_requested_at from app_private.read_account_status(%s)", (alvo,)
        ).fetchone()
    return None if row is None else row[0]


def test_o_pedido_de_exclusao_registra_e_nao_apaga(mundo: Mundo) -> None:
    # Decisão 019: apagar a identidade destrói a prova da compra. O pedido é
    # um audit com o próprio usuário como ator; a conta continua inteira.
    assert ler_status(mundo.bia, mundo.bia) is None
    quando, repetido = pedir_exclusao(mundo.bia, mundo.bia)
    assert repetido is False
    registros = audits(mundo.bia, "account.deletion.requested")
    assert len(registros) == 1
    assert registros[0]["seller"] is None
    assert registros[0]["target_id"] == mundo.bia
    assert ler_status(mundo.bia, mundo.bia) == quando
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        ainda = connection.execute(
            "select count(*) from identity_user where id=%s", (mundo.bia,)
        ).fetchone()
        acessos = connection.execute(
            "select count(*) from buyer_entitlement where buyer_identity_user_id=%s", (mundo.bia,)
        ).fetchone()
    assert ainda == (1,) and acessos == (1,)


def test_o_pedido_repetido_em_trinta_dias_devolve_o_mesmo(mundo: Mundo) -> None:
    # Modo de falha: um clique por pedido. O segundo devolve a data do
    # primeiro e não grava nada.
    primeiro, _ = pedir_exclusao(mundo.bia, mundo.bia)
    segundo, repetido = pedir_exclusao(mundo.bia, mundo.bia)
    assert repetido is True and segundo == primeiro
    assert len(audits(mundo.bia, "account.deletion.requested")) == 1


def test_o_pedido_e_o_status_recusam_quem_nao_e_o_sujeito(mundo: Mundo) -> None:
    with pytest.raises(InsufficientPrivilege):
        pedir_exclusao(mundo.ana, mundo.bia)
    with pytest.raises(InsufficientPrivilege):
        pedir_exclusao(mundo.bia, mundo.bia, capability="account.export")
    # O status é SQL puro: fora do contexto certo devolve vazio, nunca a data
    # da outra pessoa.
    assert ler_status(mundo.ana, mundo.bia) is None
    assert ler_status(mundo.bia, mundo.bia, capability="account.export") is None
    assert len(audits(mundo.bia, "account.deletion.requested")) == 1
