from __future__ import annotations

import hashlib
import hmac
import os
import time
from datetime import date
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

# Base de leads da loja (migration 0078, #619). Prova:
# - uma linha por acesso (pessoa e produto), com a situação do e-mail certa;
# - demo e pedido de exclusão aberto ficam de fora;
# - loja A não vê a B, e o consentimento de uma loja não vale na outra;
# - só dono e admin leem, com a capacidade certa;
# - o export leva só quem pode receber e-mail opcional agora, audita sem
#   e-mail e tem limite de taxa.

ADMIN_URL = os.getenv("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.getenv("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY = bytes.fromhex(os.getenv("APPSELL_TEST_CONTEXT_KEY_HEX", ""))

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY)),
    reason="isolated PostgreSQL environment is required",
)

LOJA_A = UUID("ee000000-0000-4000-8000-000000000781")
LOJA_B = UUID("ee000000-0000-4000-8000-000000000782")
DONO = UUID("ee000000-0000-4000-8000-000000000783")
EDITOR = UUID("ee000000-0000-4000-8000-000000000784")
ADMIN = UUID("ee000000-0000-4000-8000-000000000785")
PRODUTO_A1 = UUID("ee000000-0000-4000-8000-000000000786")
PRODUTO_A2 = UUID("ee000000-0000-4000-8000-000000000787")
PRODUTO_B = UUID("ee000000-0000-4000-8000-000000000788")
PUBLICACAO_A1 = UUID("ee000000-0000-4000-8000-000000000789")
PUBLICACAO_A2 = UUID("ee000000-0000-4000-8000-00000000078a")
PUBLICACAO_B = UUID("ee000000-0000-4000-8000-00000000078b")
CONSENTIU = UUID("ee000000-0000-4000-8000-000000000791")
SEM_CONSENTIMENTO = UUID("ee000000-0000-4000-8000-000000000792")
DESCADASTROU = UUID("ee000000-0000-4000-8000-000000000793")
SUPRIMIDO = UUID("ee000000-0000-4000-8000-000000000794")
PEDIU_EXCLUSAO = UUID("ee000000-0000-4000-8000-000000000795")
DEMO = UUID("ee000000-0000-4000-8000-000000000796")
ISSUER = "https://identity.test.autonomia.site"
LEITURA = "growth.leads.read"
EXPORT = "growth.leads.export"
OUTUBRO = (date(2026, 10, 1), date(2026, 10, 31))

Conexao = psycopg.Connection[tuple[object, ...]]


def _email(pessoa: UUID) -> str:
    return f"lead-{str(pessoa)[-3:]}@example.test"


def _pessoa(connection: Conexao, pessoa: UUID, nome: str | None = None) -> None:
    connection.execute(
        "insert into identity_user(id,identity_issuer,identity_sub,email_normalized,"
        "email_verified,display_name) values(%s,%s,%s,%s,true,%s) on conflict do nothing",
        (pessoa, ISSUER, f"leads-{pessoa}", _email(pessoa), nome),
    )


def _acesso(
    connection: Conexao,
    loja: UUID,
    pessoa: UUID,
    produto: UUID,
    publicacao: UUID,
    quando: str = "2026-10-05T12:00:00Z",
    origem: str = "billing",
) -> None:
    acesso = uuid4()
    connection.execute(
        "insert into buyer_entitlement(seller_organization_id,id,buyer_identity_user_id,"
        "catalog_product_id,publication_id,reference_key,source,status,created_at) "
        "values(%s,%s,%s,%s,%s,%s,%s,'active',%s) on conflict do nothing",
        (loja, acesso, pessoa, produto, publicacao, f"leads:{acesso.hex}", origem, quando),
    )


def _consentimento(connection: Conexao, loja: UUID, pessoa: UUID, situacao: str) -> None:
    revogado = "now()" if situacao == "revoked" else "null"
    connection.execute(
        "insert into app_private.email_consent(scope_id,email_normalized,purpose,status,source,"
        "text_version,text_sha256,identity_user_id,granted_at,revoked_at) "
        f"values(%s,%s,'store_news',%s,'account',1,repeat('a',64),%s,now(),{revogado}) "
        "on conflict do nothing",
        (loja, _email(pessoa), situacao, pessoa),
    )


def _loja(
    connection: Conexao, loja: UUID, slug: str, produtos: tuple[tuple[UUID, UUID], ...]
) -> None:
    connection.execute(
        "insert into seller_organization(id,slug,display_name,status) "
        "values(%s,%s,%s,'active') on conflict do nothing",
        (loja, slug, slug),
    )
    connection.execute(
        "insert into seller_membership(seller_organization_id,identity_user_id,role,status) "
        "values(%s,%s,'seller_owner','active') on conflict do nothing",
        (loja, DONO),
    )
    for indice, (produto, publicacao) in enumerate(produtos, start=1):
        connection.execute(
            "insert into catalog_product(seller_organization_id,id,slug,title,description,status)"
            " values(%s,%s,%s,%s,'Produto','draft') on conflict do nothing",
            (loja, produto, f"curso-{indice}", f"Curso {slug} {indice}"),
        )
        connection.execute(
            "insert into publication(seller_organization_id,id,catalog_product_id,version,"
            "schema_version,source_sha256,snapshot,actor_identity_user_id) "
            "values(%s,%s,%s,1,2,repeat('0',64),%s,%s) on conflict do nothing",
            (loja, publicacao, produto, Jsonb({}), DONO),
        )


@pytest.fixture(scope="module", autouse=True)
def base() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        for pessoa in (DONO, EDITOR, ADMIN):
            _pessoa(connection, pessoa)
        for pessoa in (CONSENTIU, SEM_CONSENTIMENTO, DESCADASTROU, SUPRIMIDO, PEDIU_EXCLUSAO, DEMO):
            _pessoa(connection, pessoa, "=HYPERLINK(1)" if pessoa == CONSENTIU else "Pessoa")
        _loja(
            connection,
            LOJA_A,
            "leads-a",
            ((PRODUTO_A1, PUBLICACAO_A1), (PRODUTO_A2, PUBLICACAO_A2)),
        )
        _loja(connection, LOJA_B, "leads-b", ((PRODUTO_B, PUBLICACAO_B),))
        for pessoa, papel in ((EDITOR, "seller_editor"), (ADMIN, "seller_admin")):
            connection.execute(
                "insert into seller_membership(seller_organization_id,identity_user_id,role,"
                "status) values(%s,%s,%s,'active') on conflict do nothing",
                (LOJA_A, pessoa, papel),
            )
        # Loja A: CONSENTIU com dois produtos; os outros com um. 02:30Z de 02/10
        # é 23:30 de 01/10 em São Paulo.
        _acesso(connection, LOJA_A, CONSENTIU, PRODUTO_A1, PUBLICACAO_A1)
        _acesso(connection, LOJA_A, CONSENTIU, PRODUTO_A2, PUBLICACAO_A2, "2026-10-02T02:30:00Z")
        for pessoa in (SEM_CONSENTIMENTO, DESCADASTROU, SUPRIMIDO, PEDIU_EXCLUSAO):
            _acesso(connection, LOJA_A, pessoa, PRODUTO_A1, PUBLICACAO_A1)
        _acesso(connection, LOJA_A, DEMO, PRODUTO_A1, PUBLICACAO_A1, origem="demo_seed")
        # Loja B: CONSENTIU comprou lá também, sem consentimento na B.
        _acesso(connection, LOJA_B, CONSENTIU, PRODUTO_B, PUBLICACAO_B)
        _consentimento(connection, LOJA_A, CONSENTIU, "granted")
        _consentimento(connection, LOJA_A, DESCADASTROU, "revoked")
        _consentimento(connection, LOJA_A, SUPRIMIDO, "granted")
        _consentimento(connection, LOJA_A, PEDIU_EXCLUSAO, "granted")
        _consentimento(connection, LOJA_A, DEMO, "granted")
        connection.execute(
            "insert into app_private.email_suppression(scope_id,email_sha256,reason) "
            "values(%s,app_private.email_sha256(%s),'unsubscribed') on conflict do nothing",
            (LOJA_A, _email(DESCADASTROU)),
        )
        connection.execute(
            "insert into app_private.email_suppression_global(email_sha256,reason) "
            "values(app_private.email_sha256(%s),'complaint') on conflict do nothing",
            (_email(SUPRIMIDO),),
        )
        connection.execute(
            "insert into audit_event(actor_identity_user_id,action,target_type,target_id,"
            "result,request_id) values(%s,'account.deletion.requested','identity_user',%s,"
            "'succeeded',%s)",
            (PEDIU_EXCLUSAO, PEDIU_EXCLUSAO, uuid4()),
        )
        connection.commit()


def _context(connection: Conexao, actor: UUID, capability: str, seller: UUID) -> None:
    issued = int(time.time())
    expires = issued + 60
    payload = f"{actor}:{seller}:{capability}:{issued}:{expires}"
    signature = hmac.new(CONTEXT_KEY, payload.encode(), hashlib.sha256).hexdigest()
    connection.execute(
        "select app_private.apply_request_context(%s,%s,%s,%s,%s,%s)",
        (actor, seller, capability, issued, expires, signature),
    )


def _leitura(
    loja: UUID = LOJA_A,
    periodo: tuple[date, date] = OUTUBRO,
    produto: UUID | None = None,
    pessoa: UUID = DONO,
    capacidade: str = LEITURA,
) -> dict[str, Any]:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, pessoa, capacidade, loja)
        row = connection.execute(
            "select app_private.read_store_leads(%s,%s,%s,%s,%s)",
            (pessoa, loja, periodo[0], periodo[1], produto),
        ).fetchone()
    assert row is not None
    return cast(dict[str, Any], row[0])


def _export(loja: UUID = LOJA_A, pessoa: UUID = DONO) -> dict[str, Any]:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, pessoa, EXPORT, loja)
        row = connection.execute(
            "select app_private.export_store_leads(%s,%s,%s,%s,null,%s)",
            (pessoa, loja, OUTUBRO[0], OUTUBRO[1], uuid4()),
        ).fetchone()
        connection.commit()
    assert row is not None
    return cast(dict[str, Any], row[0])


def _situacoes(resultado: dict[str, Any]) -> set[tuple[str, str, str]]:
    return {
        (linha["email"], linha["product_title"], linha["email_status"])
        for linha in resultado["rows"]
    }


def test_uma_linha_por_acesso_com_a_situacao_do_email() -> None:
    leitura = _leitura()
    assert _situacoes(leitura) == {
        (_email(CONSENTIU), "Curso leads-a 1", "granted"),
        (_email(CONSENTIU), "Curso leads-a 2", "granted"),
        (_email(SEM_CONSENTIMENTO), "Curso leads-a 1", "none"),
        (_email(DESCADASTROU), "Curso leads-a 1", "unsubscribed"),
        (_email(SUPRIMIDO), "Curso leads-a 1", "suppressed"),
    }
    assert leitura["total_rows"] == 5
    assert leitura["total_people"] == 4
    assert leitura["marketing_people"] == 1


def test_demo_e_pedido_de_exclusao_aberto_ficam_de_fora() -> None:
    emails = {linha["email"] for linha in _leitura()["rows"]}
    assert _email(DEMO) not in emails
    assert _email(PEDIU_EXCLUSAO) not in emails


def test_filtra_por_produto_e_pelo_dia_de_sao_paulo() -> None:
    assert _situacoes(_leitura(produto=PRODUTO_A2)) == {
        (_email(CONSENTIU), "Curso leads-a 2", "granted")
    }
    primeiro_dia = _leitura(periodo=(date(2026, 10, 1), date(2026, 10, 1)))
    assert [linha["since"] for linha in primeiro_dia["rows"]] == ["2026-10-01"]


def test_loja_b_nao_ve_a_a_e_o_consentimento_nao_atravessa_lojas() -> None:
    assert _situacoes(_leitura(loja=LOJA_B)) == {(_email(CONSENTIU), "Curso leads-b 1", "none")}
    assert _export(loja=LOJA_B)["rows"] == []
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, DONO, LEITURA, LOJA_B)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                "select app_private.read_store_leads(%s,%s,%s,%s,null)",
                (DONO, LOJA_A, OUTUBRO[0], OUTUBRO[1]),
            )


def test_so_dono_e_admin_leem_e_a_capacidade_e_conferida() -> None:
    assert _leitura(pessoa=ADMIN)["total_rows"] == 5
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _leitura(pessoa=EDITOR)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _leitura(capacidade=EXPORT)


def test_periodo_invertido_ou_longo_demais_e_recusado() -> None:
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        _leitura(periodo=(date(2026, 10, 2), date(2026, 10, 1)))
    assert _leitura(periodo=(date(2025, 10, 1), date(2026, 10, 1)))["rows"] is not None
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        _leitura(periodo=(date(2025, 10, 1), date(2026, 10, 2)))


def test_o_export_leva_so_quem_pode_receber_e_bate_com_a_regra_do_envio() -> None:
    export = _export()
    # Uma linha por pessoa: os dois produtos de quem consentiu vão juntos, e o
    # e-mail não se repete. "Desde" é a primeira entrada (01/10 em São Paulo).
    assert export["rows"] == [
        {
            "name": "=HYPERLINK(1)",
            "email": _email(CONSENTIU),
            "products": "Curso leads-a 1; Curso leads-a 2",
            "since": "2026-10-01",
        }
    ]
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        for linha in export["rows"]:
            row = connection.execute(
                "select app_private.email_optional_block_reason(%s,%s,'store_news')",
                (LOJA_A, linha["email"]),
            ).fetchone()
            assert row is not None and row[0] is None


def test_o_export_audita_sem_email() -> None:
    _export()
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select metadata::text from audit_event where seller_organization_id=%s "
            "and action='growth.leads_exported' and actor_identity_user_id=%s "
            "order by created_at desc limit 1",
            (LOJA_A, DONO),
        ).fetchone()
    assert row is not None
    metadata = cast(str, row[0])
    assert '"audience": "marketing"' in metadata and '"rows": 1' in metadata
    assert "@" not in metadata


def test_o_export_tem_limite_de_taxa() -> None:
    erro = None
    for _ in range(8):
        try:
            _export(pessoa=ADMIN)
        except psycopg.errors.DatabaseError as falha:
            erro = falha
            break
    assert erro is not None and erro.sqlstate == "P4291"


def test_as_funcoes_fixam_o_search_path_com_pg_temp_no_fim() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        linhas = connection.execute(
            "select p.proname, p.prosecdef, array_to_string(p.proconfig, ',') "
            "from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
            "where n.nspname = 'app_private' "
            "and p.proname in ('read_store_leads','export_store_leads') order by p.proname",
        ).fetchall()
    assert linhas == [
        ("export_store_leads", True, "search_path=pg_catalog, app_private, public, pg_temp"),
        ("read_store_leads", True, "search_path=pg_catalog, app_private, public, pg_temp"),
    ]
