"""Os três bloqueios da revisão da #553, provados pelos caminhos reais do editor.

1. O produto criado depois da 0067, pelo mesmo insert do editor
   (`update_product_course_offer_draft`), tem a oferta padrão e aparece na
   vitrine.
2. Uma oferta `draft` como padrão nunca vira acesso grátis enquanto outra
   oferta do produto estiver à venda: `grant_public_product_access` recusa com
   P4222. A segunda oferta só existe na fase 2; aqui ela é simulada dentro de
   uma transação desfeita, sem a unique antiga.
3. Publicar com uma oferta que não é a padrão é recusado (55000).
"""

from __future__ import annotations

from uuid import uuid4

import psycopg
import pytest
from test_postgres_access_links import (
    ADMIN_URL,
    CONTEXT_KEY_HEX,
    RUNTIME_URL,
    apply_context,
    buyer_id,
    create_graph,
    digest,
    publish_with_access,
    save_draft,
    seed_identities,
)

__all__ = ["seed_identities"]  # fixture autouse importada: semeia as identidades

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY_HEX)),
    reason="isolated PostgreSQL environment is required",
)


def _vitrine_tem(slug: str, product_id: str) -> bool:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        linha = connection.execute(
            "select app_private.read_public_storefront(%s)", (slug,)
        ).fetchone()
    assert linha is not None and linha[0] is not None
    return product_id in [p["id"] for p in linha[0]["products"]]


def _slug_da_loja(seller_id: object) -> str:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        linha = connection.execute(
            "select slug from seller_organization where id=%s", (seller_id,)
        ).fetchone()
    assert linha is not None
    return str(linha[0])


def test_produto_novo_pelo_editor_tem_oferta_padrao_e_aparece_na_vitrine() -> None:
    seller_id, product_id, course_id = create_graph()
    offer_id = save_draft(seller_id, product_id, course_id)
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        padrao = connection.execute(
            "select is_default from offer where seller_organization_id=%s and id=%s",
            (seller_id, offer_id),
        ).fetchone()
    assert padrao == (True,)

    publish_with_access(seller_id, product_id, course_id, offer_id, access_mode="free")
    assert _vitrine_tem(_slug_da_loja(seller_id), str(product_id))


def test_rascunho_padrao_nao_vira_acesso_gratis_com_outra_oferta_a_venda() -> None:
    seller_id, product_id, course_id = create_graph()
    offer_id = save_draft(seller_id, product_id, course_id)
    publish_with_access(seller_id, product_id, course_id, offer_id, access_mode="free")

    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        # Estado da fase 2, desfeito no fim: a padrão é rascunho, e uma
        # segunda oferta (não padrão) está à venda.
        connection.execute("alter table offer drop constraint offer_seller_product_key")
        connection.execute(
            "insert into offer(seller_organization_id,id,catalog_product_id,title,status,"
            "unit_amount_minor,is_default) values(%s,%s,%s,'Oferta à venda','active',9700,false)",
            (seller_id, uuid4(), product_id),
        )
        apply_context(connection, buyer_id(11), None, "library.enroll")
        with pytest.raises(psycopg.errors.Error) as recusa:
            connection.execute(
                "select entitlement_id from app_private.grant_public_product_access("
                "%s, %s, %s, %s, %s, %s)",
                (buyer_id(11), seller_id, product_id, uuid4(), digest("gratis?"), uuid4()),
            ).fetchone()
        connection.rollback()
    assert recusa.value.sqlstate == "P4222"


def test_publicar_com_oferta_que_nao_e_a_padrao_e_recusado() -> None:
    seller_id, product_id, course_id = create_graph()
    offer_id = save_draft(seller_id, product_id, course_id)
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "update offer set is_default=false where seller_organization_id=%s and id=%s",
            (seller_id, offer_id),
        )
        connection.commit()

    with pytest.raises(psycopg.errors.Error) as recusa:
        publish_with_access(seller_id, product_id, course_id, offer_id, access_mode="free")
    assert recusa.value.sqlstate == "55000"
    assert "catalog offer is not the default" in str(recusa.value)
