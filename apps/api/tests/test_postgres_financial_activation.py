"""Migration 0064: a loja ativa as próprias vendas pelo Financial (#382).

Cada teste diz o defeito que pega. Esta função grava PARA QUEM a compra é
entregue: um portão frouxo aqui não corrompe dado, entrega a venda de uma loja
pelo canal de outra ou mexe no vínculo que já vende.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from uuid import UUID, uuid4

import psycopg
import pytest

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.environ.get("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY = bytes.fromhex(os.environ.get("APPSELL_TEST_CONTEXT_KEY_HEX", "") or "")

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, os.environ.get("APPSELL_TEST_CONTEXT_KEY_HEX"))),
    reason="isolated PostgreSQL environment is required",
)

RUN = uuid4().hex[:8]
ACTOR = UUID("20000000-0000-4000-8000-000000000382")
SHARED_OPERATOR = uuid4()
SHARED_CATALOG_ITEM = uuid4()
READ = "commerce.payout.read"
MANAGE = "commerce.payout.manage"
INSUFFICIENT_PRIVILEGE = "42501"

Connection = psycopg.Connection[tuple[object, ...]]


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _channel(connection: Connection) -> tuple[str, str]:
    """O canal pela identidade que a 0059 grava; cria um se o banco não tem."""
    rows = connection.execute(
        "select distinct endpoint.endpoint_key_sha256,endpoint.secret_reference "
        "from webhook_endpoint endpoint join checkout_integration integration "
        "on integration.seller_organization_id=endpoint.seller_organization_id "
        "and integration.id=endpoint.checkout_integration_id "
        "where integration.adapter='generic_hmac_v1' "
        "and integration.display_name='Financial checkout' "
        "and endpoint.status='active' and integration.status='active'"
    ).fetchall()
    assert len(rows) <= 1, "o banco de teste tem mais de um canal Financial"
    if rows:
        return str(rows[0][0]), str(rows[0][1])
    owner = _seller(connection, "canal")
    channel = (_sha(f"canal-{RUN}"), f"ativa-canal-{RUN}")
    _endpoint(connection, owner, channel)
    return channel


def _seller(connection: Connection, label: str) -> UUID:
    row = connection.execute(
        "insert into seller_organization(slug,display_name,status) "
        "values(%s,%s,'active') returning id",
        (f"ativa-{label}-{RUN}", f"Ativa {label}"),
    ).fetchone()
    assert row is not None
    return UUID(str(row[0]))


def _endpoint(connection: Connection, seller: UUID, channel: tuple[str, str]) -> UUID:
    """Endpoint no canal, copiando hash e secret_reference, como a 0059."""
    integration = uuid4()
    connection.execute(
        "insert into checkout_integration(seller_organization_id,id,adapter,display_name,"
        "status) values(%s,%s,'generic_hmac_v1','Financial checkout','active')",
        (seller, integration),
    )
    connection.execute(
        "insert into webhook_endpoint(seller_organization_id,checkout_integration_id,"
        "endpoint_key_sha256,secret_reference,status) values(%s,%s,%s,%s,'active')",
        (seller, integration, *channel),
    )
    return integration


def _product(connection: Connection, seller: UUID, slug: str) -> UUID:
    product = uuid4()
    connection.execute("set role appsell_owner")
    connection.execute(
        "insert into catalog_product(seller_organization_id,id,slug,title,description)"
        " values(%s,%s,%s,'Produto','Produto')",
        (seller, product, slug),
    )
    connection.execute("reset role")
    return product


def _context(connection: Connection, capability: str, seller: UUID | None) -> None:
    issued = int(time.time())
    expires = issued + 60
    payload = f"{ACTOR}:{seller or ''}:{capability}:{issued}:{expires}"
    signature = hmac.new(CONTEXT_KEY, payload.encode(), hashlib.sha256).hexdigest()
    connection.execute(
        "select app_private.apply_request_context(%s,%s,%s,%s,%s,%s)",
        (ACTOR, seller, capability, issued, expires, signature),
    )


def _activate(
    seller: UUID,
    *,
    context_seller: UUID | None = None,
    capability: str = MANAGE,
    require_payout_ready: bool = False,
) -> str:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, capability, context_seller or seller)
        row = connection.execute(
            "select app_private.activate_seller_financial(%s,%s,%s,%s,%s)",
            (seller, SHARED_OPERATOR, SHARED_CATALOG_ITEM, require_payout_ready, uuid4()),
        ).fetchone()
    assert row is not None
    return str(row[0])


def _situation(seller: UUID) -> tuple[bool, bool, bool]:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, READ, seller)
        row = connection.execute(
            "select bound,payout_ready,channel_ready "
            "from app_private.resolve_seller_financial_activation(%s)",
            (seller,),
        ).fetchone()
    assert row is not None
    return bool(row[0]), bool(row[1]), bool(row[2])


def _snapshot(seller: UUID) -> dict[str, list[tuple[object, ...]]]:
    """Tudo o que a ativação poderia tocar numa loja, para comparar antes e depois."""
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:

        def rows(sql: str) -> list[tuple[object, ...]]:
            return sorted(connection.execute(sql, (seller,)).fetchall(), key=str)

        return {
            "seller": rows(
                "select financial_operator_id,financial_product_catalog_item_id,updated_at "
                "from seller_organization where id=%s"
            ),
            "integrations": rows(
                "select id,adapter,status from checkout_integration where seller_organization_id=%s"
            ),
            "endpoints": rows(
                "select id,checkout_integration_id,endpoint_key_sha256,secret_reference,"
                "status from webhook_endpoint where seller_organization_id=%s"
            ),
            "mappings": rows(
                "select checkout_integration_id,external_product_id_sha256,catalog_product_id "
                "from external_product_mapping where seller_organization_id=%s"
            ),
            "audit": rows(
                "select action,result from audit_event where seller_organization_id=%s "
                "and action='seller_financial.activated'"
            ),
        }


@pytest.fixture(scope="module")
def world() -> dict[str, UUID | str]:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "insert into identity_user(id,identity_issuer,identity_sub,"
            "email_normalized,email_verified) "
            "values(%s,'https://identity.test.autonomia.site','ativa-actor',"
            "'ativa.actor@example.test',true) on conflict do nothing",
            (ACTOR,),
        )
        channel = _channel(connection)
        # No papel da Hub2You: vínculo gravado à mão, com par próprio, endpoint
        # no canal e produto mapeado.
        hub = _seller(connection, "hub")
        connection.execute(
            "update seller_organization set financial_operator_id=%s,"
            "financial_product_catalog_item_id=%s where id=%s",
            (uuid4(), uuid4(), hub),
        )
        _endpoint(connection, hub, channel)
        _product(connection, hub, "curso-hub")
        world: dict[str, UUID | str] = {"channel": channel[0], "hub": hub}
        for label in ("a", "b", "replay", "exige", "exige-pronta", "contexto", "ambiguo"):
            world[label] = _seller(connection, label)
        for label in ("a", "b"):
            seller = world[label]
            assert isinstance(seller, UUID)
            world[f"{label}-p1"] = _product(connection, seller, "produto-um")
            world[f"{label}-p2"] = _product(connection, seller, "produto-dois")
        pronta = world["exige-pronta"]
        connection.execute(
            "insert into seller_payout_account(seller_organization_id,provider,"
            "provider_account_id,charges_enabled) values(%s,'stripe',%s,true)",
            (pronta, f"acct_ativa{RUN}"),
        )
        connection.commit()
    return world


def _id(world: dict[str, UUID | str], key: str) -> UUID:
    value = world[key]
    assert isinstance(value, UUID)
    return value


def test_schema_ready_knows_0064(world: dict[str, UUID | str]) -> None:
    # A API não sobe se o readiness não aprender a versão e as funções.
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        assert connection.execute("select app_private.schema_ready()").fetchone() == (True,)
        assert connection.execute(
            "select to_regprocedure("
            "'app_private.resolve_seller_financial_activation(uuid)') is not null,"
            "to_regprocedure("
            "'app_private.activate_seller_financial(uuid,uuid,uuid,boolean,uuid)') is not null"
        ).fetchone() == (True, True)


def test_the_existing_hub2you_binding_comes_back_untouched(world: dict[str, UUID | str]) -> None:
    # Pega a ativação sobrescrevendo um vínculo que já vende, com o par global.
    hub = _id(world, "hub")
    before = _snapshot(hub)

    assert _activate(hub) == "already_active"
    assert _activate(hub, require_payout_ready=True) == "already_active"

    assert _snapshot(hub) == before
    assert _situation(hub)[0] is True


def test_activation_binds_the_shared_pair_and_the_channel(world: dict[str, UUID | str]) -> None:
    # Pega a loja ativada sem endpoint no canal ou sem mapping: a compra
    # morreria em dead_letter, como no #465.
    seller = _id(world, "a")
    assert _situation(seller) == (False, False, True)

    assert _activate(seller) == "activated"

    after = _snapshot(seller)
    assert after["seller"][0][:2] == (SHARED_OPERATOR, SHARED_CATALOG_ITEM)
    endpoints = after["endpoints"]
    assert len(endpoints) == 1
    integration = endpoints[0][1]
    assert endpoints[0][2] == world["channel"]
    products = {_id(world, "a-p1"), _id(world, "a-p2")}
    assert after["mappings"] == sorted(((integration, _sha(str(p)), p) for p in products), key=str)
    assert after["audit"] == [("seller_financial.activated", "succeeded")]
    assert _situation(seller)[0] is True


def test_a_product_created_after_activation_is_mapped(world: dict[str, UUID | str]) -> None:
    # Pega a ativação que deixa a loja fora do trigger da 0062.
    seller = _id(world, "a")
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        product = _product(connection, seller, "depois-da-ativacao")
    mapped = [row for row in _snapshot(seller)["mappings"] if row[2] == product]
    assert len(mapped) == 1


def test_replay_writes_nothing_twice(world: dict[str, UUID | str]) -> None:
    # Pega o duplo clique criando segundo endpoint ou segunda auditoria.
    seller = _id(world, "replay")
    assert _activate(seller) == "activated"
    first = _snapshot(seller)

    assert _activate(seller) == "already_active"

    assert _snapshot(seller) == first


def test_activating_a_never_touches_b(world: dict[str, UUID | str]) -> None:
    # Pega a ativação de uma loja escrevendo na outra.
    b = _id(world, "b")
    assert _snapshot(b)["endpoints"] == []
    assert _snapshot(b)["mappings"] == []
    assert _situation(b)[0] is False


def test_a_store_cannot_activate_another(world: dict[str, UUID | str]) -> None:
    # Pega o portão de contexto: o envelope é da loja A, o alvo é a loja B.
    b = _id(world, "b")
    before = _snapshot(b)
    with pytest.raises(psycopg.Error) as raised:
        _activate(b, context_seller=_id(world, "a"))
    assert raised.value.sqlstate == INSUFFICIENT_PRIVILEGE
    assert _snapshot(b) == before


def test_read_capability_cannot_activate(world: dict[str, UUID | str]) -> None:
    # Pega a leitura (seller_admin) conseguindo escrever.
    seller = _id(world, "contexto")
    with pytest.raises(psycopg.Error) as raised:
        _activate(seller, capability=READ)
    assert raised.value.sqlstate == INSUFFICIENT_PRIVILEGE
    assert _situation(seller)[0] is False


def test_the_439_precondition_refuses_without_a_ready_account(
    world: dict[str, UUID | str],
) -> None:
    # Pega a flag da #439 ligada e a loja sem conta pronta mesmo assim ativada.
    seller = _id(world, "exige")
    before = _snapshot(seller)

    assert _activate(seller, require_payout_ready=True) == "payout_account_not_ready"

    assert _snapshot(seller) == before


def test_the_439_precondition_passes_with_a_ready_account(
    world: dict[str, UUID | str],
) -> None:
    seller = _id(world, "exige-pronta")
    assert _situation(seller) == (False, True, True)
    assert _activate(seller, require_payout_ready=True) == "activated"


def test_an_ambiguous_channel_fails_loud_and_writes_nothing(
    world: dict[str, UUID | str],
) -> None:
    # Pega a ativação escolhendo um canal entre dois: levanta 55000, não grava, e a
    # leitura mostra a loja bloqueada em vez de quebrar a página.
    assert ADMIN_URL
    seller = _id(world, "ambiguo")
    before = _snapshot(seller)
    with psycopg.connect(ADMIN_URL) as connection:
        intruder = _seller(connection, "intruso")
        _endpoint(connection, intruder, (_sha(f"intruso-{RUN}"), f"intruso-{RUN}"))
        connection.commit()
    try:
        with pytest.raises(psycopg.Error) as raised:
            _activate(seller)
        assert raised.value.sqlstate == "55000"
        assert _snapshot(seller) == before
        assert _situation(seller) == (False, False, False)
    finally:
        with psycopg.connect(ADMIN_URL) as connection:
            connection.execute(
                "update webhook_endpoint set status='disabled' where seller_organization_id=%s",
                (intruder,),
            )
            connection.commit()
    assert _situation(seller) == (False, False, True)


def test_runtime_still_cannot_write_the_binding_directly(world: dict[str, UUID | str]) -> None:
    # Pega um grant a mais: o vínculo só muda pela função.
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        privileges = connection.execute(
            "select has_table_privilege('appsell_runtime','public.seller_organization',p) "
            "from unnest(array['update','insert','delete']) p"
        ).fetchall()
    assert [row[0] for row in privileges] == [False, False, False]
