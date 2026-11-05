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
        for tabela in (
            "email_consent",
            "email_unsubscribe_token",
            "email_suppression",
        ):
            connection.execute(
                f"delete from app_private.{tabela} where email_normalized in (%s,%s,%s)",  # noqa: S608
                (EMAIL, EMAIL_SUPRIMIDO, EMAIL_SEM_CONSENTIMENTO),
            )
        connection.execute(
            "delete from app_private.email_suppression_global where email_normalized in (%s,%s)",
            (EMAIL, EMAIL_SUPRIMIDO),
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
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(
                    "select app_private.record_email_consent(%s,'store_news',1::smallint,"
                    "'account',%s)",
                    (LOJA_A, uuid4()),
                )


def test_finalidade_da_plataforma_nao_aceita_loja() -> None:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, COMPRADOR, CONSENT, LOJA_A)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
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
        "insert into app_private.email_suppression_global(email_normalized,reason) "
        "values(%s,'complaint') on conflict do nothing",
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


def test_transacional_sai_mesmo_para_endereco_suprimido() -> None:
    _registrar_worker()
    assert ADMIN_URL and RUNTIME_URL
    with psycopg.connect(ADMIN_URL) as connection:
        _suprimir_globalmente(connection, EMAIL_SUPRIMIDO)
        item = _enfileirar(connection, "purchase_confirmed", '{"schema_version":1}')
        connection.commit()
    vistos: set[str] = set()
    for _ in range(64):
        with psycopg.connect(RUNTIME_URL) as connection:
            _context(connection, WORKER, WORKER_CAPABILITY)
            row = connection.execute(
                "select app_private.claim_email_outbox(%s,%s,8,30)", (WORKER, EMAIL_LABEL)
            ).fetchone()
            connection.commit()
        lote = cast(list[dict[str, object]], row[0] if row else [])
        if not lote:
            break
        vistos.update(str(entrada["id"]) for entrada in lote)
    assert str(item) in vistos


def test_opcional_sem_permissao_nunca_chega_ao_worker() -> None:
    # Hoje a constraint de kind só aceita transacional. A constraint cai dentro
    # desta transação, que volta atrás no fim: prova a regra do claim para o
    # primeiro tipo opcional que existir.
    _registrar_worker()
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "alter table app_private.email_outbox drop constraint email_outbox_kind_check"
        )
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
        f"select app_private.email_optional_block_reason('{LOJA_A}','a@b.test','store_news')",
    ):
        with psycopg.connect(RUNTIME_URL) as connection:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(consulta)
