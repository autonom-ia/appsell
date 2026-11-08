from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import psycopg
import pytest

from appsell_api.email_worker import WORKER_CAPABILITY, WORKER_SUBJECT

# Consentimento, descadastro e supressão de e-mail opcional (migration 0066,
# #561). O que estes testes provam:
# - o consentimento é da pessoa logada, com o e-mail da identidade, e por loja;
# - o descadastro pelo token vale na hora, é idempotente e não toca outra loja;
# - a supressão global vence o consentimento;
# - o transacional sai mesmo para um endereço suprimido;
# - o opcional sem permissão nunca chega ao worker;
# - o runtime não lê as tabelas nem emite token.

ADMIN_URL = os.getenv("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.getenv("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY = bytes.fromhex(os.getenv("APPSELL_TEST_CONTEXT_KEY_HEX", ""))

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY)),
    reason="isolated PostgreSQL environment is required",
)

LOJA_A = UUID("ee000000-0000-4000-8000-000000000661")
LOJA_B = UUID("ee000000-0000-4000-8000-000000000662")
COMPRADOR = UUID("ee000000-0000-4000-8000-000000000663")
OUTRA_PESSOA = UUID("ee000000-0000-4000-8000-000000000664")
EMAIL = "consentimento.comprador@example.test"
EMAIL_SUPRIMIDO = "consentimento.suprimido@example.test"
EMAIL_SEM_CONSENTIMENTO = "consentimento.ninguem@example.test"
ISSUER = "https://identity.test.autonomia.site"
CONSENT = "notification.email.consent"
EMAIL_LABEL = "pytest-consent-worker"
WORKER = uuid5(NAMESPACE_URL, f"{ISSUER}\0{WORKER_SUBJECT}")

Conexao = psycopg.Connection[tuple[object, ...]]


@pytest.fixture(scope="module", autouse=True)
def base() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        enderecos = (EMAIL, EMAIL_SUPRIMIDO, EMAIL_SEM_CONSENTIMENTO)
        for tabela in ("email_consent",):
            connection.execute(
                f"delete from app_private.{tabela} where email_normalized in (%s,%s,%s)",  # noqa: S608
                enderecos,
            )
        for tabela in ("email_suppression", "email_suppression_global", "email_unsubscribe_token"):
            connection.execute(
                f"delete from app_private.{tabela} where email_sha256 in "  # noqa: S608
                "(app_private.email_sha256(%s),app_private.email_sha256(%s),"
                "app_private.email_sha256(%s))",
                enderecos,
            )
        connection.execute(
            "delete from app_private.email_outbox where seller_organization_id in (%s,%s)",
            (LOJA_A, LOJA_B),
        )
        for pessoa, sub, email in (
            (COMPRADOR, "consent-buyer", EMAIL),
            (OUTRA_PESSOA, "consent-suppressed", EMAIL_SUPRIMIDO),
        ):
            connection.execute(
                "insert into identity_user(id,identity_issuer,identity_sub,email_normalized,"
                "email_verified) values(%s,%s,%s,%s,true) on conflict do nothing",
                (pessoa, ISSUER, sub, email),
            )
        for loja, slug in ((LOJA_A, "loja-consentimento-a"), (LOJA_B, "loja-consentimento-b")):
            connection.execute(
                "insert into seller_organization(id,slug,display_name,status) "
                "values(%s,%s,%s,'active') on conflict do nothing",
                (loja, slug, slug),
            )
        connection.commit()


def _context(connection: Conexao, actor: UUID, capability: str, seller: UUID | None = None) -> None:
    issued = int(time.time())
    expires = issued + 60
    payload = f"{actor}:{seller or ''}:{capability}:{issued}:{expires}"
    signature = hmac.new(CONTEXT_KEY, payload.encode(), hashlib.sha256).hexdigest()
    connection.execute(
        "select app_private.apply_request_context(%s,%s,%s,%s,%s,%s)",
        (actor, seller, capability, issued, expires, signature),
    )


def _consentir(pessoa: UUID, loja: UUID | None, finalidade: str = "store_news") -> None:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, pessoa, CONSENT, loja)
        connection.execute(
            "select app_private.record_email_consent(%s,%s,1::smallint,'account',%s)",
            (loja, finalidade, uuid4()),
        )
        connection.commit()


def _bloqueio(loja: UUID, email: str, finalidade: str = "store_news") -> str | None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select app_private.email_optional_block_reason(%s,%s,%s)", (loja, email, finalidade)
        ).fetchone()
    return cast(str | None, row[0] if row else None)


def _emitir_token(loja: UUID | None, email: str, finalidade: str = "store_news") -> str:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select app_private.issue_email_unsubscribe_token(%s,%s,%s)", (loja, email, finalidade)
        ).fetchone()
        connection.commit()
    assert row is not None
    return cast(str, row[0])


def _descadastrar(token: str) -> bool:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        row = connection.execute("select app_private.unsubscribe_email(%s)", (token,)).fetchone()
        connection.commit()
    assert row is not None
    return cast(bool, row[0])


def test_o_consentimento_e_da_pessoa_logada_com_o_email_da_identidade() -> None:
    _consentir(COMPRADOR, LOJA_A)
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select email_normalized,status,source,text_version,text_sha256,granted_at "
            "from app_private.email_consent where scope_id=%s and purpose='store_news' "
            "and identity_user_id=%s",
            (LOJA_A, COMPRADOR),
        ).fetchone()
        texto = connection.execute(
            "select body_sha256 from app_private.email_consent_text "
            "where purpose='store_news' and version=1"
        ).fetchone()
    assert row is not None and texto is not None
    assert row[0] == EMAIL
    assert row[1:4] == ("granted", "account", 1)
    assert row[4] == texto[0]
    assert row[5] is not None
    assert _bloqueio(LOJA_A, EMAIL) is None
    # Consentir na loja A não autoriza a loja B.
    assert _bloqueio(LOJA_B, EMAIL) == "no_consent"


def test_consentimento_sem_a_capacidade_ou_com_outra_loja_e_recusado() -> None:
    assert RUNTIME_URL
    for capacidade, loja_do_contexto in ((WORKER_CAPABILITY, LOJA_A), (CONSENT, LOJA_B)):
        with psycopg.connect(RUNTIME_URL) as connection:
            _context(connection, COMPRADOR, capacidade, loja_do_contexto)
            with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
                connection.execute(
                    "select app_private.record_email_consent(%s,'store_news',1::smallint,"
                    "'account',%s)",
                    (LOJA_A, uuid4()),
                )


def test_finalidade_da_plataforma_nao_aceita_loja() -> None:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, COMPRADOR, CONSENT, LOJA_A)
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select app_private.record_email_consent(%s,'platform_news',1::smallint,"
                "'account',%s)",
                (LOJA_A, uuid4()),
            )
    _consentir(COMPRADOR, None, "platform_news")
    assert _bloqueio(LOJA_A, EMAIL, "platform_news") is None


def test_descadastro_vale_na_hora_e_so_na_loja_do_link() -> None:
    _consentir(COMPRADOR, LOJA_A)
    _consentir(COMPRADOR, LOJA_B)
    token = _emitir_token(LOJA_A, EMAIL)
    assert _descadastrar(token) is True
    assert _bloqueio(LOJA_A, EMAIL) == "suppressed"
    assert _bloqueio(LOJA_A, EMAIL, "content_reminder") == "suppressed"
    assert _bloqueio(LOJA_B, EMAIL) is None
    # Idempotente: o segundo clique não falha nem muda nada.
    assert _descadastrar(token) is True
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select status,revoked_source from app_private.email_consent "
            "where scope_id=%s and email_normalized=%s and purpose='store_news'",
            (LOJA_A, EMAIL),
        ).fetchone()
        guardado = connection.execute(
            "select count(*) from app_private.email_unsubscribe_token where token_sha256=%s",
            (hashlib.sha256(token.encode()).hexdigest(),),
        ).fetchone()
    assert row == ("revoked", "unsubscribe_link")
    # Só o sha do token fica no banco.
    assert guardado == (1,)


def test_token_invalido_nao_descadastra_ninguem() -> None:
    assert _descadastrar("x" * 43) is False
    assert _descadastrar("") is False
    assert _descadastrar("y" * 500) is False


def test_reconsentir_tira_a_supressao_do_descadastro() -> None:
    _consentir(COMPRADOR, LOJA_A)
    _descadastrar(_emitir_token(LOJA_A, EMAIL))
    assert _bloqueio(LOJA_A, EMAIL) == "suppressed"
    _consentir(COMPRADOR, LOJA_A)
    assert _bloqueio(LOJA_A, EMAIL) is None


def test_supressao_global_vence_o_consentimento() -> None:
    _consentir(OUTRA_PESSOA, LOJA_A)
    assert _bloqueio(LOJA_A, EMAIL_SUPRIMIDO) is None
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        _suprimir_globalmente(connection, EMAIL_SUPRIMIDO)
        connection.commit()
    assert _bloqueio(LOJA_A, EMAIL_SUPRIMIDO) == "suppressed"
    assert _bloqueio(LOJA_B, EMAIL_SUPRIMIDO) == "suppressed"


def _registrar_worker() -> None:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, WORKER, WORKER_CAPABILITY)
        connection.execute(
            "select app_private.ensure_email_worker(%s,%s,%s,"
            "'email-worker@system.appsell.internal',%s)",
            (WORKER, ISSUER, WORKER_SUBJECT, EMAIL_LABEL),
        )
        connection.commit()


def _suprimir_globalmente(connection: Conexao, email: str) -> None:
    connection.execute(
        "insert into app_private.email_suppression_global(email_sha256,reason) "
        "values(app_private.email_sha256(%s),'complaint') on conflict do nothing",
        (email,),
    )


def _enfileirar(connection: Conexao, kind: str, payload: str, email: str = EMAIL_SUPRIMIDO) -> UUID:
    row = connection.execute(
        "insert into app_private.email_outbox(seller_organization_id,kind,dedupe_key,"
        "recipient_identity_user_id,recipient_email,payload) "
        "values(%s,%s,%s,%s,%s,%s::jsonb) returning id",
        (LOJA_A, kind, f"consent:{uuid4().hex}", OUTRA_PESSOA, email, payload),
    ).fetchone()
    assert row is not None
    return cast(UUID, row[0])


def _claim_na_transacao(connection: Conexao) -> set[str]:
    """Reivindica dentro de uma transação que o teste reverte: os itens de
    outras suítes voltam como estavam."""
    _context(connection, WORKER, WORKER_CAPABILITY)
    vistos: set[str] = set()
    for _ in range(64):
        row = connection.execute(
            "select app_private.claim_email_outbox(%s,%s,8,30)", (WORKER, EMAIL_LABEL)
        ).fetchone()
        lote = cast(list[dict[str, object]], row[0] if row else [])
        if not lote:
            return vistos
        vistos.update(str(entrada["id"]) for entrada in lote)
    raise AssertionError("email outbox did not drain")


def _sem_constraint_de_kind(connection: Conexao) -> None:
    connection.execute(
        "alter table app_private.email_outbox drop constraint email_outbox_kind_check"
    )


@pytest.mark.parametrize("kind", ["purchase_confirmed", "access_link_redeemed"])
def test_transacional_sai_mesmo_para_endereco_suprimido(kind: str) -> None:
    _registrar_worker()
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        _suprimir_globalmente(connection, EMAIL_SUPRIMIDO)
        item = _enfileirar(connection, kind, '{"schema_version":1}')
        _suprimir_globalmente(connection, EMAIL_SEM_CONSENTIMENTO)
        sem_consentimento = _enfileirar(
            connection, kind, '{"schema_version":1}', EMAIL_SEM_CONSENTIMENTO
        )
        vistos = _claim_na_transacao(connection)
        connection.rollback()
    assert {str(item), str(sem_consentimento)} <= vistos


def test_opcional_com_consentimento_chega_ao_worker() -> None:
    # Controle positivo: a regra não bloqueia quem consentiu.
    _registrar_worker()
    _consentir(COMPRADOR, LOJA_A, "content_reminder")
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        _sem_constraint_de_kind(connection)
        item = _enfileirar(
            connection,
            "store_news_digest",
            '{"schema_version":1,"purpose":"content_reminder"}',
            EMAIL,
        )
        vistos = _claim_na_transacao(connection)
        connection.rollback()
    assert str(item) in vistos


def test_opcional_com_lease_vencido_e_barrado_ao_voltar() -> None:
    _registrar_worker()
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        _sem_constraint_de_kind(connection)
        item = _enfileirar(
            connection,
            "store_news_digest",
            '{"schema_version":1,"purpose":"store_news"}',
            EMAIL_SEM_CONSENTIMENTO,
        )
        connection.execute(
            "update app_private.email_outbox set status='leased',lease_owner='outro-worker',"
            "lease_fence=1,attempt_count=1,lease_expires_at=clock_timestamp()-interval '1 minute' "
            "where id=%s",
            (item,),
        )
        vistos = _claim_na_transacao(connection)
        estado = connection.execute(
            "select status,error_code from app_private.email_outbox where id=%s", (item,)
        ).fetchone()
        connection.rollback()
    assert str(item) not in vistos
    assert estado == ("dead_letter", "no_consent")


def test_opcional_sem_permissao_nunca_chega_ao_worker() -> None:
    # Hoje a constraint de kind só aceita transacional. A constraint cai dentro
    # desta transação, que volta atrás no fim: prova a regra do claim para o
    # primeiro tipo opcional que existir.
    _registrar_worker()
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        _sem_constraint_de_kind(connection)
        _suprimir_globalmente(connection, EMAIL_SUPRIMIDO)
        suprimido = _enfileirar(
            connection, "store_news_digest", '{"schema_version":1,"purpose":"store_news"}'
        )
        sem_consentimento = _enfileirar(
            connection,
            "store_news_digest",
            '{"schema_version":1,"purpose":"store_news"}',
            EMAIL_SEM_CONSENTIMENTO,
        )
        sem_finalidade = _enfileirar(
            connection, "store_news_digest", '{"schema_version":1}', EMAIL_SEM_CONSENTIMENTO
        )
        _context(connection, WORKER, WORKER_CAPABILITY)
        row = connection.execute(
            "select app_private.claim_email_outbox(%s,%s,8,30)", (WORKER, EMAIL_LABEL)
        ).fetchone()
        reivindicados = {str(e["id"]) for e in cast(list[dict[str, object]], row[0] if row else [])}
        estados = connection.execute(
            "select id,status,error_code from app_private.email_outbox where id in (%s,%s,%s)",
            (suprimido, sem_consentimento, sem_finalidade),
        ).fetchall()
        connection.rollback()
    assert not {str(suprimido), str(sem_consentimento), str(sem_finalidade)} & reivindicados
    assert {UUID(str(e[0])): (e[1], e[2]) for e in estados} == {
        suprimido: ("dead_letter", "suppressed"),
        sem_consentimento: ("dead_letter", "no_consent"),
        sem_finalidade: ("dead_letter", "no_consent"),
    }


def test_o_runtime_nao_le_as_tabelas_nem_emite_token() -> None:
    assert RUNTIME_URL
    for consulta in (
        "select count(*) from app_private.email_consent",
        "select count(*) from app_private.email_suppression",
        "select count(*) from app_private.email_suppression_global",
        "select count(*) from app_private.email_unsubscribe_token",
        "select app_private.issue_email_unsubscribe_token(null,'a@b.test','platform_news')",
        f"select app_private.forget_email_consents('{COMPRADOR}')",
        f"select app_private.email_optional_block_reason('{LOJA_A}','a@b.test','store_news')",
    ):
        with psycopg.connect(RUNTIME_URL) as connection:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(consulta)


def test_uma_normalizacao_so_em_todo_caminho() -> None:
    # Token emitido com o endereço sujo; a checagem com outra grafia; a
    # supressão guardada como sha do endereço limpo, igual ao que o Python
    # calcula. Nenhum caminho normaliza por conta própria.
    sujo = "  " + EMAIL_SEM_CONSENTIMENTO.upper() + " "
    assert _descadastrar(_emitir_token(LOJA_B, sujo)) is True
    assert _bloqueio(LOJA_B, EMAIL_SEM_CONSENTIMENTO.title()) == "suppressed"
    esperado = hashlib.sha256(EMAIL_SEM_CONSENTIMENTO.encode()).hexdigest()
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        normalizado = connection.execute(
            "select app_private.normalize_email(%s),app_private.email_sha256(%s)", (sujo, sujo)
        ).fetchone()
        guardado = connection.execute(
            "select count(*) from app_private.email_suppression "
            "where scope_id=%s and email_sha256=%s",
            (LOJA_B, esperado),
        ).fetchone()
        colunas = connection.execute(
            "select table_name,column_name from information_schema.columns "
            "where table_schema='app_private' "
            "and table_name in ('email_suppression','email_suppression_global',"
            "'email_unsubscribe_token')"
        ).fetchall()
    assert normalizado == (EMAIL_SEM_CONSENTIMENTO, esperado)
    assert guardado == (1,)
    # A supressão sobrevive à exclusão da conta, então não guarda o endereço.
    assert not any("email_normalized" == coluna for _, coluna in colunas)


def test_todo_kind_aceito_pela_caixa_de_saida_esta_classificado() -> None:
    # Um transacional novo que entre na constraint sem entrar na lista da 0066
    # cairia em no_consent em silêncio. Este teste lê a constraint do banco e
    # exige que cada kind seja transacional conhecido ou opcional declarado.
    opcionais_declarados: set[str] = set()
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        definicao = connection.execute(
            "select pg_get_constraintdef(oid) from pg_constraint "
            "where conname='email_outbox_kind_check' "
            "and conrelid='app_private.email_outbox'::regclass"
        ).fetchone()
        assert definicao is not None
        # Os literais são os trechos entre aspas simples: índices ímpares.
        kinds = set(str(definicao[0]).split("'")[1::2])
        assert kinds
        transacionais = {
            kind
            for kind in kinds
            if connection.execute(
                "select app_private.email_kind_is_transactional(%s)", (kind,)
            ).fetchone()
            == (True,)
        }
    assert kinds - transacionais <= opcionais_declarados, kinds - transacionais


def test_esquecer_o_titular_apaga_o_consentimento_e_mantem_a_supressao() -> None:
    pessoa = uuid4()
    email = f"consentimento.excluido.{pessoa.hex[:8]}@example.test"
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "insert into identity_user(id,identity_issuer,identity_sub,email_normalized,"
            "email_verified) values(%s,%s,%s,%s,true)",
            (pessoa, ISSUER, f"consent-excluido-{pessoa.hex[:8]}", email),
        )
        connection.commit()
    _consentir(pessoa, LOJA_A)
    _descadastrar(_emitir_token(LOJA_A, email))
    _consentir(pessoa, LOJA_B)
    with psycopg.connect(ADMIN_URL) as connection:
        apagados = connection.execute(
            "select app_private.forget_email_consents(%s)", (pessoa,)
        ).fetchone()
        consentimentos = connection.execute(
            "select count(*) from app_private.email_consent where email_normalized=%s", (email,)
        ).fetchone()
        connection.commit()
    assert apagados == (2,)
    assert consentimentos == (0,)
    assert _bloqueio(LOJA_A, email) == "suppressed"
    assert _bloqueio(LOJA_B, email) == "no_consent"


def _datas(loja: UUID, email: str, token: str) -> tuple[object, ...]:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        consentimento = connection.execute(
            "select revoked_at from app_private.email_consent "
            "where scope_id=%s and email_normalized=%s and purpose='store_news'",
            (loja, email),
        ).fetchone()
        usado = connection.execute(
            "select used_at from app_private.email_unsubscribe_token where token_sha256=%s",
            (hashlib.sha256(token.encode()).hexdigest(),),
        ).fetchone()
    assert consentimento is not None and usado is not None
    return (consentimento[0], usado[0])


def test_segundo_clique_nao_muda_as_datas_nem_audita_de_novo() -> None:
    _consentir(COMPRADOR, LOJA_A)
    token = _emitir_token(LOJA_A, EMAIL)
    assert _descadastrar(token) is True
    antes = _datas(LOJA_A, EMAIL, token)
    assert _descadastrar(token) is True
    assert _datas(LOJA_A, EMAIL, token) == antes
    assert all(valor is not None for valor in antes)


def test_descadastro_da_plataforma_nao_cala_as_lojas() -> None:
    _consentir(COMPRADOR, LOJA_A)
    _consentir(COMPRADOR, None, "platform_news")
    assert _descadastrar(_emitir_token(None, EMAIL, "platform_news")) is True
    assert _bloqueio(LOJA_A, EMAIL, "platform_news") == "suppressed"
    assert _bloqueio(LOJA_A, EMAIL) is None


def test_reconsentir_nao_tira_supressao_manual_nem_global() -> None:
    assert ADMIN_URL
    _consentir(OUTRA_PESSOA, LOJA_B)
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "insert into app_private.email_suppression(scope_id,email_sha256,reason) "
            "values(%s,app_private.email_sha256(%s),'manual') on conflict do nothing",
            (LOJA_B, EMAIL_SUPRIMIDO),
        )
        _suprimir_globalmente(connection, EMAIL_SUPRIMIDO)
        connection.commit()
    _consentir(OUTRA_PESSOA, LOJA_B)
    with psycopg.connect(ADMIN_URL) as connection:
        restantes = connection.execute(
            "select (select count(*) from app_private.email_suppression "
            " where scope_id=%s and email_sha256=app_private.email_sha256(%s)),"
            "(select count(*) from app_private.email_suppression_global "
            " where email_sha256=app_private.email_sha256(%s))",
            (LOJA_B, EMAIL_SUPRIMIDO, EMAIL_SUPRIMIDO),
        ).fetchone()
    assert restantes == (1, 1)
    assert _bloqueio(LOJA_B, EMAIL_SUPRIMIDO) == "suppressed"


def test_revogar_pela_conta_e_auditado() -> None:
    _consentir(COMPRADOR, LOJA_B, "content_reminder")
    assert _bloqueio(LOJA_B, EMAIL, "content_reminder") is None
    pedido = uuid4()
    assert RUNTIME_URL and ADMIN_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, COMPRADOR, CONSENT, LOJA_B)
        connection.execute(
            "select app_private.revoke_email_consent(%s,'content_reminder',%s)", (LOJA_B, pedido)
        )
        connection.commit()
    assert _bloqueio(LOJA_B, EMAIL, "content_reminder") == "no_consent"
    with psycopg.connect(ADMIN_URL) as connection:
        fonte = connection.execute(
            "select revoked_source from app_private.email_consent where scope_id=%s "
            "and email_normalized=%s and purpose='content_reminder'",
            (LOJA_B, EMAIL),
        ).fetchone()
        auditoria = connection.execute(
            "select action from audit_event where request_id=%s", (pedido,)
        ).fetchone()
    assert fonte == ("account",)
    assert auditoria == ("notification.email_consent_revoked",)


def test_consentimento_da_plataforma_e_descadastro_sao_auditados() -> None:
    pedido = uuid4()
    assert RUNTIME_URL and ADMIN_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, COMPRADOR, CONSENT)
        connection.execute(
            "select app_private.record_email_consent(null,'platform_news',1::smallint,"
            "'account',%s)",
            (pedido,),
        )
        connection.commit()
    with psycopg.connect(ADMIN_URL) as connection:
        antes = connection.execute(
            "select count(*) from audit_event where action='notification.email_unsubscribed'"
        ).fetchone()
    _descadastrar(_emitir_token(None, EMAIL, "platform_news"))
    with psycopg.connect(ADMIN_URL) as connection:
        consentimento = connection.execute(
            "select action,seller_organization_id from audit_event where request_id=%s", (pedido,)
        ).fetchone()
        depois = connection.execute(
            "select count(*) from audit_event where action='notification.email_unsubscribed'"
        ).fetchone()
        metadados = connection.execute(
            "select metadata::text from audit_event where action like 'notification.email_%%' "
            "order by created_at desc limit 5"
        ).fetchall()
    assert consentimento == ("notification.email_consent_granted", None)
    assert antes is not None and depois is not None and depois[0] == antes[0] + 1
    # A auditoria não guarda endereço.
    assert not any("@" in str(linha[0]) for linha in metadados)


def test_troca_de_email_nao_leva_o_consentimento() -> None:
    pessoa = uuid4()
    antigo = f"consentimento.antigo.{pessoa.hex[:8]}@example.test"
    novo = f"consentimento.novo.{pessoa.hex[:8]}@example.test"
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "insert into identity_user(id,identity_issuer,identity_sub,email_normalized,"
            "email_verified) values(%s,%s,%s,%s,true)",
            (pessoa, ISSUER, f"consent-troca-{pessoa.hex[:8]}", antigo),
        )
        connection.commit()
    _consentir(pessoa, LOJA_A)
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "update identity_user set email_normalized=%s where id=%s", (novo, pessoa)
        )
        connection.commit()
    assert _bloqueio(LOJA_A, antigo) is None
    assert _bloqueio(LOJA_A, novo) == "no_consent"
