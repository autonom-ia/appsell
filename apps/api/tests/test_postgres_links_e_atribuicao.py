from __future__ import annotations

import hashlib
import hmac
import os
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
import pytest

# Links UTM e atribuição da venda (migration 0075, #563). Prova:
# - só dono e admin da loja criam, listam e arquivam links, pelo contexto
#   assinado; a loja A não vê nem usa os links da B;
# - a atribuição respeita a janela de 7 dias, a primeira da compra fica, e o
#   link de outra loja não entra;
# - a venda aprovada encontra a campanha pelo sha da compra;
# - a contagem de aceite e recusa não guarda dado pessoal;
# - o runtime não lê as tabelas direto, e as funções fixam pg_temp.

ADMIN_URL = os.getenv("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.getenv("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY = bytes.fromhex(os.getenv("APPSELL_TEST_CONTEXT_KEY_HEX", ""))

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY)),
    reason="isolated PostgreSQL environment is required",
)

LOJA_A = UUID("ee000000-0000-4000-8000-000000000751")
LOJA_B = UUID("ee000000-0000-4000-8000-000000000752")
LOJA_INATIVA = UUID("ee000000-0000-4000-8000-000000000753")
DONO = UUID("ee000000-0000-4000-8000-000000000754")
EDITOR = UUID("ee000000-0000-4000-8000-000000000755")
INTEGRACAO_A = UUID("ee000000-0000-4000-8000-000000000756")
ENDPOINT_A = UUID("ee000000-0000-4000-8000-000000000757")
PRODUTO_A = UUID("ee000000-0000-4000-8000-000000000758")
ISSUER = "https://identity.test.autonomia.site"
LEITURA = "growth.campaign_link.read"
ESCRITA = "growth.campaign_link.write"

Conexao = psycopg.Connection[tuple[object, ...]]


def _sha(valor: str) -> str:
    return hashlib.sha256(valor.encode()).hexdigest()


@pytest.fixture(scope="module", autouse=True)
def base() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        for loja in (LOJA_A, LOJA_B, LOJA_INATIVA):
            connection.execute(
                "delete from app_private.checkout_attribution where seller_organization_id=%s",
                (loja,),
            )
            connection.execute(
                "delete from app_private.cookie_consent_tally where seller_organization_id=%s",
                (loja,),
            )
            connection.execute(
                "delete from app_private.campaign_link where seller_organization_id=%s", (loja,)
            )
            connection.execute("delete from webhook_event where seller_organization_id=%s", (loja,))
        for pessoa, sub in ((DONO, "links-owner"), (EDITOR, "links-editor")):
            connection.execute(
                "insert into identity_user(id,identity_issuer,identity_sub,email_normalized,"
                "email_verified) values(%s,%s,%s,%s,true) on conflict do nothing",
                (pessoa, ISSUER, sub, f"{sub}@example.test"),
            )
        for loja, slug, situacao in (
            (LOJA_A, "loja-links-a", "active"),
            (LOJA_B, "loja-links-b", "active"),
            (LOJA_INATIVA, "loja-links-inativa", "suspended"),
        ):
            connection.execute(
                "insert into seller_organization(id,slug,display_name,status) "
                "values(%s,%s,%s,%s) on conflict do nothing",
                (loja, slug, slug, situacao),
            )
            connection.execute(
                "insert into seller_membership(seller_organization_id,identity_user_id,"
                "role,status) values(%s,%s,'seller_owner','active') on conflict do nothing",
                (loja, DONO),
            )
        connection.execute(
            "insert into seller_membership(seller_organization_id,identity_user_id,role,status) "
            "values(%s,%s,'seller_editor','active') on conflict do nothing",
            (LOJA_A, EDITOR),
        )
        connection.execute(
            "insert into catalog_product(seller_organization_id,id,slug,title,description,status) "
            "values(%s,%s,'curso-links','Curso Links','Produto','draft') on conflict do nothing",
            (LOJA_A, PRODUTO_A),
        )
        connection.execute(
            "insert into checkout_integration(seller_organization_id,id,adapter,display_name,"
            "status) values(%s,%s,'generic_hmac_v1','Checkout','active') on conflict do nothing",
            (LOJA_A, INTEGRACAO_A),
        )
        connection.execute(
            "insert into webhook_endpoint(seller_organization_id,id,checkout_integration_id,"
            "endpoint_key_sha256,secret_reference,status) values(%s,%s,%s,%s,%s,'active') "
            "on conflict do nothing",
            (LOJA_A, ENDPOINT_A, INTEGRACAO_A, _sha("endpoint:links-a"), "links-a"),
        )
        connection.commit()


def _evento(evento: str, tipo: str, compra: str, quando: str) -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "insert into webhook_event(seller_organization_id,checkout_integration_id,"
            "webhook_endpoint_id,external_event_id_sha256,external_event_id_octets,"
            "payload_sha256,payload_octets,event_type,external_product_id_sha256,"
            "external_product_id_octets,external_purchase_id_sha256,"
            "external_purchase_id_octets,occurred_at,signed_at) "
            "values(%s,%s,%s,%s,8,%s,10,%s,%s,8,%s,8,%s,%s) on conflict do nothing",
            (
                LOJA_A,
                INTEGRACAO_A,
                ENDPOINT_A,
                _sha(evento),
                _sha(f"payload:{evento}"),
                tipo,
                _sha("produto-links"),
                _sha(compra),
                quando,
                quando,
            ),
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


def _chamar(
    sql: str,
    params: tuple[object, ...],
    *,
    pessoa: UUID = DONO,
    capacidade: str = ESCRITA,
    loja: UUID = LOJA_A,
) -> Any:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, pessoa, capacidade, loja)
        row = connection.execute(sql, params).fetchone()
        connection.commit()
    assert row is not None
    return row[0]


def _criar(
    loja: UUID = LOJA_A,
    nome: str = "Instagram outubro",
    destino: str | None = None,
    fonte: str | None = "instagram",
    campanha: str | None = "outubro",
    pessoa: UUID = DONO,
    capacidade: str = ESCRITA,
) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        _chamar(
            "select app_private.create_campaign_link(%s,%s,%s,%s,%s,'social',%s,null,null,%s)",
            (pessoa, loja, nome, destino, fonte, campanha, uuid4()),
            pessoa=pessoa,
            capacidade=capacidade,
            loja=loja,
        ),
    )


def _listar(loja: UUID = LOJA_A) -> list[dict[str, Any]]:
    resultado = _chamar(
        "select app_private.list_campaign_links(%s,%s)",
        (DONO, loja),
        capacidade=LEITURA,
        loja=loja,
    )
    return cast(list[dict[str, Any]], resultado["links"])


def _atribuir(
    compra: str,
    link: UUID | None,
    visita: datetime,
    loja: UUID = LOJA_A,
    fonte: str | None = "instagram",
    campanha: str | None = "outubro",
) -> bool:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        row = connection.execute(
            "select app_private.record_checkout_attribution(%s,%s,%s,%s,'social',%s,null,null,%s)",
            (loja, _sha(compra), link, fonte, campanha, visita),
        ).fetchone()
        connection.commit()
    assert row is not None
    return bool(row[0])


def _agora() -> datetime:
    return datetime.now(UTC)


def test_dono_cria_e_lista_o_link_da_propria_loja() -> None:
    link = _criar(destino="curso-links")
    assert link["utm_source"] == "instagram" and link["destination_page_slug"] == "curso-links"
    assert link["status"] == "active"
    assert link["id"] in {item["id"] for item in _listar()}
    assert link["id"] not in {item["id"] for item in _listar(LOJA_B)}


def test_editor_capacidade_errada_e_outra_loja_sao_recusados() -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _criar(pessoa=EDITOR)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _criar(capacidade=LEITURA)
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, DONO, ESCRITA, LOJA_B)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                "select app_private.create_campaign_link("
                "%s,%s,'x',null,'a',null,null,null,null,%s)",
                (DONO, LOJA_A, uuid4()),
            )


@pytest.mark.parametrize(
    ("destino", "fonte", "campanha", "nome"),
    [
        ("pagina-que-nao-existe", "instagram", "outubro", "Link"),
        (None, None, "outubro", "Link"),
        (None, "x" * 101, "outubro", "Link"),
        (None, "insta\u0007gram", "outubro", "Link"),
        (None, " instagram", "outubro", "Link"),
        (None, "instagram", "outubro", ""),
        ("Curso_Links", "instagram", "outubro", "Link"),
    ],
)
def test_link_invalido_e_recusado(
    destino: str | None, fonte: str | None, campanha: str | None, nome: str
) -> None:
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        _criar(destino=destino, fonte=fonte, campanha=campanha, nome=nome)


def test_arquivar_e_idempotente_e_so_na_propria_loja() -> None:
    link = UUID(_criar(nome="Para arquivar")["id"])
    arquivar = "select app_private.archive_campaign_link(%s,%s,%s,%s)"
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, DONO, ESCRITA, LOJA_B)
        row = connection.execute(arquivar, (DONO, LOJA_B, link, uuid4())).fetchone()
        assert row == (False,)
    assert _chamar(arquivar, (DONO, LOJA_A, link, uuid4())) is True
    assert _chamar(arquivar, (DONO, LOJA_A, link, uuid4())) is False
    situacao = {item["id"]: item["status"] for item in _listar()}
    assert situacao[str(link)] == "archived"


def test_atribuicao_respeita_a_janela_e_a_primeira_fica() -> None:
    link = UUID(_criar(nome="Janela")["id"])
    assert _atribuir("janela-1", link, _agora() - timedelta(days=6, hours=23)) is True
    assert _atribuir("janela-1", None, _agora(), fonte="outra") is False
    assert _atribuir("janela-2", link, _agora() - timedelta(days=7, minutes=1)) is False
    assert _atribuir("janela-3", link, _agora() + timedelta(minutes=5)) is False
    assert _atribuir("janela-4", None, _agora(), fonte=None, campanha=None) is False
    assert _atribuir("janela-5", None, _agora(), loja=LOJA_INATIVA) is False
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select link_id,utm_source from app_private.checkout_attribution "
            "where seller_organization_id=%s and purchase_sha256=%s",
            (LOJA_A, _sha("janela-1")),
        ).fetchone()
    assert row == (link, "instagram")


def test_link_de_outra_loja_nao_entra_na_atribuicao() -> None:
    link_b = UUID(_criar(loja=LOJA_B, nome="Da loja B")["id"])
    assert _atribuir("cruzada", link_b, _agora()) is True
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select link_id from app_private.checkout_attribution "
            "where seller_organization_id=%s and purchase_sha256=%s",
            (LOJA_A, _sha("cruzada")),
        ).fetchone()
    assert row == (None,)


def test_venda_aprovada_encontra_a_campanha_pelo_sha_da_compra() -> None:
    link = UUID(_criar(nome="Relatório", campanha="relatorio")["id"])
    hoje = _agora()
    assert _atribuir("rel-1", link, hoje, campanha="relatorio") is True
    assert _atribuir("rel-2", link, hoje, campanha="relatorio") is True
    _evento("rel-e1", "purchase.approved", "rel-1", hoje.isoformat())
    _evento("rel-e1", "purchase.approved", "rel-1", hoje.isoformat())
    _evento("rel-e2", "purchase.approved", "rel-2", hoje.isoformat())
    _evento("rel-e3", "purchase.refunded", "rel-2", hoje.isoformat())
    # Venda sem atribuição não aparece no relatório por campanha.
    _evento("rel-e4", "purchase.approved", "rel-sem", hoje.isoformat())
    dia = hoje.date()
    relatorio = _chamar(
        "select app_private.read_campaign_sales(%s,%s,%s,%s)",
        (DONO, LOJA_A, dia - timedelta(days=1), dia + timedelta(days=1)),
        capacidade=LEITURA,
    )
    linhas = [linha for linha in relatorio["rows"] if linha["utm_campaign"] == "relatorio"]
    assert linhas == [
        {
            "link_id": str(link),
            "link_name": "Relatório",
            "utm_source": "instagram",
            "utm_medium": "social",
            "utm_campaign": "relatorio",
            "approved": 2,
            "refunded": 1,
        }
    ]
    texto = str(relatorio)
    for proibido in ("@", "identity", "email", _sha("rel-1")):
        assert proibido not in texto


def test_relatorio_por_campanha_confere_periodo_e_contexto() -> None:
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        _chamar(
            "select app_private.read_campaign_sales(%s,%s,%s,%s)",
            (DONO, LOJA_A, date(2025, 10, 1), date(2026, 10, 2)),
            capacidade=LEITURA,
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _chamar(
            "select app_private.read_campaign_sales(%s,%s,%s,%s)",
            (EDITOR, LOJA_A, date(2026, 10, 1), date(2026, 10, 2)),
            pessoa=EDITOR,
            capacidade=LEITURA,
        )


def test_contagem_de_consentimento_soma_sem_dado_pessoal() -> None:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        for aceitou in (True, True, False):
            row = connection.execute(
                "select app_private.record_cookie_consent(%s,'v1',%s)", (LOJA_A, aceitou)
            ).fetchone()
            assert row == (True,)
        assert connection.execute(
            "select app_private.record_cookie_consent(%s,'V1;drop',true)", (LOJA_A,)
        ).fetchone() == (False,)
        assert connection.execute(
            "select app_private.record_cookie_consent(%s,'v1',true)", (LOJA_INATIVA,)
        ).fetchone() == (False,)
        connection.commit()
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select sum(accepted),sum(refused) from app_private.cookie_consent_tally "
            "where seller_organization_id=%s and text_version='v1'",
            (LOJA_A,),
        ).fetchone()
        colunas = {
            linha[0]
            for linha in connection.execute(
                "select column_name from information_schema.columns "
                "where table_schema='app_private' and table_name in "
                "('cookie_consent_tally','checkout_attribution')"
            ).fetchall()
        }
    assert row == (2, 1)
    for coluna in colunas:
        for proibido in ("email", "identity", "ip", "agent", "visitor"):
            assert proibido not in str(coluna)


def test_teto_de_links_ativos_por_loja() -> None:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, DONO, ESCRITA, LOJA_B)
        with pytest.raises(psycopg.errors.DatabaseError) as falha:
            for indice in range(205):
                connection.execute(
                    "select app_private.create_campaign_link(%s,%s,%s,null,'teto',null,null,"
                    "null,null,%s)",
                    (DONO, LOJA_B, f"Link {indice}", uuid4()),
                )
        connection.rollback()
    assert falha.value.sqlstate == "P4130"


def test_runtime_nao_le_as_tabelas_direto() -> None:
    assert RUNTIME_URL
    for consulta in (
        "select count(*) from app_private.campaign_link",
        "select count(*) from app_private.checkout_attribution",
        "select count(*) from app_private.cookie_consent_tally",
    ):
        with psycopg.connect(RUNTIME_URL) as connection:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(consulta)


def test_funcoes_fixam_o_search_path_com_pg_temp() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        linhas = connection.execute(
            "select p.proname, p.prosecdef, array_to_string(p.proconfig, ',') "
            "from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
            "where n.nspname = 'app_private' and p.proname in ("
            "'create_campaign_link','archive_campaign_link','list_campaign_links',"
            "'read_campaign_sales','record_checkout_attribution','record_cookie_consent',"
            "'purge_checkout_attribution')"
        ).fetchall()
    assert len(linhas) == 7
    for _nome, definer, configuracao in linhas:
        assert definer is True
        assert configuracao == "search_path=pg_catalog, app_private, public, pg_temp"


def _atribuicao_antiga(compra: str, idade: timedelta) -> None:
    assert ADMIN_URL
    quando = _agora() - idade
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "insert into app_private.checkout_attribution(seller_organization_id,purchase_sha256,"
            "utm_source,visited_at,created_at) values(%s,%s,'retencao',%s,%s)",
            (LOJA_A, _sha(compra), quando, quando),
        )
        connection.commit()


def _existe(compra: str) -> bool:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select count(*) from app_private.checkout_attribution "
            "where seller_organization_id=%s and purchase_sha256=%s",
            (LOJA_A, _sha(compra)),
        ).fetchone()
    return row is not None and row[0] == 1


def _limpar(connection: Conexao) -> dict[str, Any]:
    row = connection.execute("select app_private.purge_checkout_attribution(5000)").fetchone()
    assert row is not None
    return cast(dict[str, Any], row[0])


def test_limpeza_apaga_sem_venda_em_48h_e_tudo_depois_de_13_meses() -> None:
    _atribuicao_antiga("ret-sem-venda", timedelta(hours=49))
    _atribuicao_antiga("ret-recente", timedelta(hours=47))
    _atribuicao_antiga("ret-com-venda", timedelta(days=30))
    _evento("ret-e1", "purchase.approved", "ret-com-venda", _agora().isoformat())
    _atribuicao_antiga("ret-velha", timedelta(days=400))
    _evento("ret-e2", "purchase.approved", "ret-velha", _agora().isoformat())
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        resultado = _limpar(connection)
        connection.commit()
    assert resultado["skipped"] is False
    assert resultado["without_sale"] >= 1 and resultado["expired"] >= 1
    assert not _existe("ret-sem-venda")
    assert _existe("ret-recente")
    assert _existe("ret-com-venda")
    assert not _existe("ret-velha")


def test_duas_replicas_ao_mesmo_tempo_so_uma_limpa() -> None:
    _atribuicao_antiga("ret-replica", timedelta(hours=50))
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as primeira, psycopg.connect(RUNTIME_URL) as segunda:
        resultado_da_primeira = _limpar(primeira)
        # A primeira segura a trava até o commit; a segunda não espera.
        resultado_da_segunda = _limpar(segunda)
        segunda.commit()
        primeira.commit()
    assert resultado_da_primeira["skipped"] is False
    assert resultado_da_primeira["without_sale"] >= 1
    assert resultado_da_segunda == {"skipped": True, "without_sale": 0, "expired": 0}
    assert not _existe("ret-replica")
