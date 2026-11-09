"""O pedido do checkout no banco de verdade (#154, fatia 1, migration 0077).

Cada teste monta uma loja e um produto à venda pelos caminhos do editor
(`create_graph`, `save_draft`, `publish_with_access`) e chama as funções como o
runtime chama: conexão `appsell_runtime` com o contexto assinado do worker do
checkout, sem loja no envelope.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Any, LiteralString
from uuid import UUID, uuid4

import psycopg
import pytest
from test_postgres_access_links import (
    ADMIN_URL,
    CONTEXT_KEY_HEX,
    RUNTIME_URL,
    apply_context,
    bind_payout_account,
    create_graph,
    publish_with_access,
    save_draft,
    seed_identities,
)

__all__ = ["seed_identities"]  # fixture autouse importada: semeia as identidades

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY_HEX)),
    reason="isolated PostgreSQL environment is required",
)

WORKER = UUID("77000000-0000-4000-8000-000000000154")
CAPACIDADE = "commerce.checkout.order"
PRECO = 9700
PRECO_MUDOU = "P4095"
SEM_PRIVILEGIO = "42501"
CHECK = "23514"
DUPLICADO = "23505"
CRIA = (
    "select order_id, offer_id, offer_version, charged_minor, expires_at "
    "from app_private.create_checkout_order(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
)
LIGA = "select app_private.attach_checkout_intent(%s,%s,%s)"


def _sha(valor: str) -> str:
    return hashlib.sha256(valor.encode()).hexdigest()


def _produto_a_venda(preco: int = PRECO) -> tuple[UUID, UUID, UUID, UUID]:
    seller_id, product_id, course_id = create_graph()
    bind_payout_account(seller_id)
    offer_id = save_draft(seller_id, product_id, course_id, price=preco)
    publish_with_access(seller_id, product_id, course_id, offer_id, access_mode="paid")
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        linha = connection.execute(
            "select published_snapshot_id from catalog_product "
            "where seller_organization_id=%s and id=%s",
            (seller_id, product_id),
        ).fetchone()
    assert linha is not None
    return seller_id, product_id, UUID(str(linha[0])), offer_id


def _cria(
    seller_id: UUID,
    product_id: UUID,
    publication_id: UUID,
    *,
    preco: int = PRECO,
    moeda: str = "BRL",
    periodo: str = "one_time",
    comissao: int | None = None,
    liquido: int | None = None,
    plano: str = "launch",
    comprador: str = "buyer-sub-1",
    capacidade: str = CAPACIDADE,
    loja_no_contexto: UUID | None = None,
) -> tuple[Any, ...]:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, WORKER, loja_no_contexto, capacidade)
        linha = connection.execute(
            CRIA,
            (
                seller_id,
                product_id,
                publication_id,
                preco,
                moeda,
                periodo,
                comissao,
                liquido,
                plano,
                _sha(comprador),
            ),
        ).fetchone()
        connection.commit()
    assert linha is not None
    return linha


def _liga(seller_id: UUID, order_id: UUID, intent: str) -> bool:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, WORKER, None, CAPACIDADE)
        linha = connection.execute(LIGA, (seller_id, order_id, _sha(intent))).fetchone()
        connection.commit()
    assert linha is not None
    return bool(linha[0])


def _pedido(seller_id: UUID, order_id: UUID) -> dict[str, Any] | None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        cursor = connection.execute(
            "select * from app_private.checkout_order where seller_organization_id=%s and id=%s",
            (seller_id, order_id),
        )
        linha = cursor.fetchone()
        if linha is None:
            return None
        nomes = [coluna.name for coluna in cursor.description or []]
    return dict(zip(nomes, linha, strict=True))


def _pedidos_da_loja(seller_id: UUID) -> int:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        linha = connection.execute(
            "select count(*) from app_private.checkout_order where seller_organization_id=%s",
            (seller_id,),
        ).fetchone()
    assert linha is not None
    return int(linha[0])


def _sqlstate(acao: Any) -> str | None:
    with pytest.raises(psycopg.errors.Error) as recusa:
        acao()
    return recusa.value.sqlstate


def test_schema_ready_e_verdadeiro_depois_da_0077() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        linha = connection.execute("select app_private.schema_ready()").fetchone()
    assert linha is not None
    assert linha[0] is True


def test_o_pedido_guarda_a_oferta_padrao_e_os_valores_calculados_no_web() -> None:
    seller_id, product_id, publication_id, offer_id = _produto_a_venda()
    order_id, oferta, versao, cobrado, expira = _cria(
        seller_id, product_id, publication_id, comissao=582, liquido=PRECO - 582
    )
    assert (oferta, cobrado) == (offer_id, PRECO)
    pedido = _pedido(seller_id, order_id)
    assert pedido is not None
    assert pedido["offer_version"] == versao
    assert pedido["product_id"] == product_id
    assert pedido["status"] == "open"
    assert pedido["purchase_sha256"] is None
    assert pedido["coupon_id"] is None
    assert (pedido["unit_amount_minor"], pedido["discount_minor"]) == (PRECO, 0)
    assert (pedido["platform_fee_minor"], pedido["seller_net_minor"]) == (582, PRECO - 582)
    assert (pedido["currency"], pedido["billing_period"], pedido["plan_key"]) == (
        "BRL",
        "one_time",
        "launch",
    )
    # O comprador só em hash: o subject não fica no banco.
    assert pedido["buyer_sha256"] == _sha("buyer-sub-1")
    assert expira - pedido["created_at"] == timedelta(minutes=31)


def test_venda_sem_comissao_grava_comissao_e_liquido_nulos() -> None:
    seller_id, product_id, publication_id, _ = _produto_a_venda()
    order_id = _cria(seller_id, product_id, publication_id)[0]
    pedido = _pedido(seller_id, order_id)
    assert pedido is not None
    assert (pedido["platform_fee_minor"], pedido["seller_net_minor"]) == (None, None)


@pytest.mark.parametrize(
    "mudanca",
    [{"preco": PRECO + 1}, {"moeda": "USD"}, {"periodo": "monthly"}],
)
def test_preco_moeda_ou_periodo_diferente_da_oferta_e_recusado(mudanca: dict[str, Any]) -> None:
    # Pega: o pedido gravando um valor que não é o da oferta à venda agora.
    seller_id, product_id, publication_id, _ = _produto_a_venda()
    estado = _sqlstate(lambda: _cria(seller_id, product_id, publication_id, **mudanca))
    assert estado == PRECO_MUDOU
    assert _pedidos_da_loja(seller_id) == 0


def test_publicacao_antiga_e_recusada() -> None:
    seller_id, product_id, _, _ = _produto_a_venda()
    estado = _sqlstate(lambda: _cria(seller_id, product_id, uuid4()))
    assert estado == PRECO_MUDOU


def test_produto_de_outra_loja_nao_vira_pedido_de_nenhuma() -> None:
    # Isolamento: a loja A não consegue um pedido com o produto da loja B, nem
    # gravar na loja B com a chave da loja A.
    loja_a, _, _, _ = _produto_a_venda()
    loja_b, produto_b, publicacao_b, _ = _produto_a_venda()
    estado = _sqlstate(lambda: _cria(loja_a, produto_b, publicacao_b))
    assert estado == PRECO_MUDOU
    assert _pedidos_da_loja(loja_a) == 0
    assert _pedidos_da_loja(loja_b) == 0


def test_loja_inativa_nao_grava_pedido() -> None:
    seller_id, product_id, publication_id, _ = _produto_a_venda()
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "update seller_organization set status='suspended' where id=%s", (seller_id,)
        )
        connection.commit()
    assert _sqlstate(lambda: _cria(seller_id, product_id, publication_id)) == PRECO_MUDOU


def _admin(sql: LiteralString, parametros: tuple[Any, ...]) -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(sql, parametros)
        connection.commit()


@pytest.mark.parametrize(
    "mudanca",
    [
        "update offer set status='archived' where seller_organization_id=%s and id=%s",
        "update offer set status='draft' where seller_organization_id=%s and id=%s",
        "update offer set is_default=false where seller_organization_id=%s and id=%s",
    ],
)
def test_oferta_que_saiu_de_venda_ou_deixou_de_ser_a_padrao_e_recusada(
    mudanca: LiteralString,
) -> None:
    # Pega: o pedido gravando uma oferta que não é mais a padrão à venda.
    seller_id, product_id, publication_id, offer_id = _produto_a_venda()
    _admin(mudanca, (seller_id, offer_id))
    assert _sqlstate(lambda: _cria(seller_id, product_id, publication_id)) == PRECO_MUDOU
    assert _pedidos_da_loja(seller_id) == 0


def test_produto_despublicado_e_recusado() -> None:
    seller_id, product_id, publication_id, _ = _produto_a_venda()
    _admin(
        "update catalog_product set status='unpublished' where seller_organization_id=%s and id=%s",
        (seller_id, product_id),
    )
    assert _sqlstate(lambda: _cria(seller_id, product_id, publication_id)) == PRECO_MUDOU
    assert _pedidos_da_loja(seller_id) == 0


@pytest.mark.parametrize("com_loja", [False, True])
def test_outra_capacidade_ou_envelope_de_loja_e_recusado(com_loja: bool) -> None:
    # Pega: um envelope de usuário da loja (painel) chegando à gravação.
    seller_id, product_id, publication_id, _ = _produto_a_venda()
    if com_loja:
        estado = _sqlstate(
            lambda: _cria(seller_id, product_id, publication_id, loja_no_contexto=seller_id)
        )
    else:
        estado = _sqlstate(
            lambda: _cria(
                seller_id, product_id, publication_id, capacidade="commerce.checkout.destination"
            )
        )
    assert estado == SEM_PRIVILEGIO
    assert _pedidos_da_loja(seller_id) == 0


@pytest.mark.parametrize(
    ("comissao", "liquido"),
    [(582, None), (None, 100), (582, PRECO), (PRECO + 1, -1)],
)
def test_comissao_e_liquido_incoerentes_sao_recusados_pelo_banco(
    comissao: int | None, liquido: int | None
) -> None:
    seller_id, product_id, publication_id, _ = _produto_a_venda()
    estado = _sqlstate(
        lambda: _cria(seller_id, product_id, publication_id, comissao=comissao, liquido=liquido)
    )
    assert estado == CHECK


def test_ligar_ao_intent_uma_vez_so() -> None:
    seller_id, product_id, publication_id, _ = _produto_a_venda()
    order_id = _cria(seller_id, product_id, publication_id)[0]
    assert _liga(seller_id, order_id, "intent-1") is True
    pedido = _pedido(seller_id, order_id)
    assert pedido is not None
    assert (pedido["status"], pedido["purchase_sha256"]) == ("intent_created", _sha("intent-1"))
    # Repetir o mesmo intent é idempotente; outro intent não troca o primeiro.
    assert _liga(seller_id, order_id, "intent-1") is True
    assert _liga(seller_id, order_id, "intent-2") is False
    pedido = _pedido(seller_id, order_id)
    assert pedido is not None
    assert pedido["purchase_sha256"] == _sha("intent-1")


def test_ligar_com_a_chave_de_outra_loja_nao_acha_o_pedido() -> None:
    loja_a, produto_a, publicacao_a, _ = _produto_a_venda()
    loja_b, _, _, _ = _produto_a_venda()
    order_id = _cria(loja_a, produto_a, publicacao_a)[0]
    assert _liga(loja_b, order_id, "intent-x") is False
    pedido = _pedido(loja_a, order_id)
    assert pedido is not None
    assert (pedido["status"], pedido["purchase_sha256"]) == ("open", None)


def test_o_mesmo_intent_nao_liga_dois_pedidos_da_loja() -> None:
    seller_id, product_id, publication_id, _ = _produto_a_venda()
    primeiro = _cria(seller_id, product_id, publication_id)[0]
    segundo = _cria(seller_id, product_id, publication_id)[0]
    assert _liga(seller_id, primeiro, "intent-dup") is True
    assert _sqlstate(lambda: _liga(seller_id, segundo, "intent-dup")) == DUPLICADO


def test_o_runtime_nao_le_a_tabela_direto() -> None:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as negado:
            connection.execute("select count(*) from app_private.checkout_order")
        assert negado.value.sqlstate == SEM_PRIVILEGIO


@pytest.mark.parametrize(
    "assinatura",
    [
        "app_private.create_checkout_order(uuid,uuid,uuid,bigint,text,text,bigint,bigint,text,text)",
        "app_private.attach_checkout_intent(uuid,uuid,text)",
    ],
)
def test_postura_das_funcoes(assinatura: str) -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        linha = connection.execute(
            "select prosecdef, proconfig, pg_get_userbyid(proowner), "
            "has_function_privilege('public', oid, 'execute'), "
            "has_function_privilege('appsell_runtime', oid, 'execute') "
            "from pg_proc where oid = %s::regprocedure",
            (assinatura,),
        ).fetchone()
    assert linha is not None
    definer, config, dono, publico, runtime = linha
    assert definer is True
    assert dono == "appsell_owner"
    assert publico is False
    assert runtime is True
    search_path = next(item for item in config if item.startswith("search_path="))
    assert search_path.endswith("pg_temp")
