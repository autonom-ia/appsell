"""Minha conta do comprador (#351): a exportação é só do sujeito, e o pedido
de exclusão é um registro — não uma exclusão.

O risco desta onda é vazamento de PII: a exportação junta dado de dez
tabelas. Por isso o mundo de teste povoa TODAS elas para duas compradoras
que não têm nada em comum — um filtro errado em qualquer subconsulta
devolveria o dado da outra, e não um array vazio que passa despercebido.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
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
PDF = "application/pdf"


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


class Pessoa:
    """Uma compradora com a sua loja, o seu curso e todo o rastro que ela deixa."""

    def __init__(self, rotulo: str, sufixo: str, traco: float) -> None:
        self.rotulo = rotulo
        self.id = uuid4()
        self.dona = uuid4()
        self.loja = uuid4()
        self.produto = uuid4()
        self.publicacao = uuid4()
        self.curso = uuid4()
        self.modulo = uuid4()
        self.aula = uuid4()
        self.acesso = uuid4()
        self.matricula = uuid4()
        self.integracao = uuid4()
        self.blob = uuid4()
        self.asset = uuid4()
        self.email = f"{rotulo}-{sufixo}@example.test"
        self.nome = f"{rotulo.capitalize()} Teste {sufixo}"
        self.titulo = f"Curso da {rotulo.capitalize()} {sufixo}"
        self.nome_do_curso = f"Trilha da {rotulo.capitalize()} {sufixo}"
        self.nome_da_aula = f"Aula da {rotulo.capitalize()} {sufixo}"
        self.nome_da_loja = f"Loja da {rotulo.capitalize()} {sufixo}"
        self.compra_sha256 = hashlib.sha256(f"compra-{rotulo}-{sufixo}".encode()).hexdigest()
        self.traco = traco
        self.pagina = 7 if rotulo == "ana" else 9
        self.sufixo = sufixo

    def marcas(self) -> tuple[str, ...]:
        """Tudo que só existe no mundo desta pessoa — não pode aparecer no da outra."""
        return (
            self.email,
            self.nome,
            self.titulo,
            self.nome_do_curso,
            self.nome_da_aula,
            self.nome_da_loja,
            str(self.traco),
        )

    def semear(self, connection: psycopg.Connection[Any]) -> None:
        connection.execute(
            "insert into identity_user(id,identity_issuer,identity_sub,email_normalized,"
            "email_verified,display_name) values(%s,%s,%s,%s,true,%s),(%s,%s,%s,%s,true,null)",
            (
                self.id,
                ISSUER,
                f"{self.rotulo}-{self.sufixo}",
                self.email,
                self.nome,
                self.dona,
                ISSUER,
                f"dona-{self.rotulo}-{self.sufixo}",
                f"dona.{self.rotulo}@{self.sufixo}.example.test",
            ),
        )
        connection.execute(
            "insert into seller_organization(id,slug,display_name,status) "
            "values(%s,%s,%s,'active')",
            (self.loja, f"loja-{self.rotulo}-{self.sufixo}", self.nome_da_loja),
        )
        connection.execute(
            "insert into seller_membership(seller_organization_id,identity_user_id,role,status)"
            " values(%s,%s,'seller_owner','active')",
            (self.loja, self.dona),
        )
        connection.execute(
            "insert into catalog_product(seller_organization_id,id,slug,title,description)"
            " values(%s,%s,%s,%s,'')",
            (self.loja, self.produto, f"produto-{self.rotulo}-{self.sufixo}", self.titulo),
        )
        connection.execute(
            "insert into course(seller_organization_id,id,title,description) values(%s,%s,%s,'')",
            (self.loja, self.curso, self.nome_do_curso),
        )
        connection.execute(
            "insert into course_module(seller_organization_id,id,course_id,title,position)"
            " values(%s,%s,%s,'Módulo',0)",
            (self.loja, self.modulo, self.curso),
        )
        connection.execute(
            "insert into lesson(seller_organization_id,id,course_id,module_id,title,position)"
            " values(%s,%s,%s,%s,%s,0)",
            (self.loja, self.aula, self.curso, self.modulo, self.nome_da_aula),
        )
        connection.execute(
            "insert into publication(seller_organization_id,id,catalog_product_id,version,"
            "schema_version,source_sha256,snapshot,actor_identity_user_id) "
            "values(%s,%s,%s,1,2,repeat('0',64),%s,%s)",
            (self.loja, self.publicacao, self.produto, Jsonb({}), self.dona),
        )
        connection.execute(
            "insert into publication_course(seller_organization_id,catalog_product_id,"
            "publication_id,course_id,position,lesson_count) values(%s,%s,%s,%s,0,1)",
            (self.loja, self.produto, self.publicacao, self.curso),
        )
        connection.execute(
            "insert into publication_lesson(seller_organization_id,catalog_product_id,"
            "publication_id,course_id,module_id,lesson_id,course_position,module_position,"
            "lesson_position) values(%s,%s,%s,%s,%s,%s,0,0,0)",
            (self.loja, self.produto, self.publicacao, self.curso, self.modulo, self.aula),
        )
        connection.execute(
            "insert into buyer_entitlement(seller_organization_id,id,buyer_identity_user_id,"
            "catalog_product_id,publication_id,reference_key,source,status,progress_percent)"
            " values(%s,%s,%s,%s,%s,'pytest-minha-conta','billing','active',40)",
            (self.loja, self.acesso, self.id, self.produto, self.publicacao),
        )
        connection.execute(
            "insert into checkout_integration(seller_organization_id,id,adapter,display_name)"
            " values(%s,%s,'generic_hmac_v1','Integração')",
            (self.loja, self.integracao),
        )
        connection.execute(
            "insert into app_private.entitlement_grant(seller_organization_id,"
            "checkout_integration_id,external_purchase_id_sha256,buyer_identity_user_id,"
            "catalog_product_id,status,occurred_at) "
            "values(%s,%s,%s,%s,%s,'active',clock_timestamp())",
            (self.loja, self.integracao, self.compra_sha256, self.id, self.produto),
        )
        # O banco cria a matrícula sozinho quando a entitlement nasce (0007);
        # aqui só se lê o id dela para pendurar o progresso.
        matricula = connection.execute(
            "select id from learning_enrollment where seller_organization_id=%s "
            "and entitlement_id=%s and course_id=%s",
            (self.loja, self.acesso, self.curso),
        ).fetchone()
        assert matricula is not None
        self.matricula = matricula[0]
        connection.execute(
            "insert into course_learning_state(seller_organization_id,enrollment_id,"
            "buyer_identity_user_id,entitlement_id,catalog_product_id,publication_id,"
            "course_id,last_lesson_id,completed_lessons,total_lessons,progress_percent,"
            "state_version) values(%s,%s,%s,%s,%s,%s,%s,%s,1,1,100,1) "
            "on conflict(seller_organization_id,enrollment_id) do update set "
            "last_lesson_id=excluded.last_lesson_id,completed_lessons=1,total_lessons=1,"
            "progress_percent=100",
            (
                self.loja,
                self.matricula,
                self.id,
                self.acesso,
                self.produto,
                self.publicacao,
                self.curso,
                self.aula,
            ),
        )
        connection.execute(
            "insert into lesson_progress(seller_organization_id,enrollment_id,"
            "buyer_identity_user_id,entitlement_id,catalog_product_id,publication_id,"
            "course_id,lesson_id,progress_percent,completed_at) "
            "values(%s,%s,%s,%s,%s,%s,%s,%s,100,clock_timestamp()) "
            "on conflict(seller_organization_id,enrollment_id,lesson_id) do update set "
            "progress_percent=100,completed_at=clock_timestamp()",
            (
                self.loja,
                self.matricula,
                self.id,
                self.acesso,
                self.produto,
                self.publicacao,
                self.curso,
                self.aula,
            ),
        )
        connection.execute(
            "insert into asset_blob(seller_organization_id,id,storage_driver,storage_bucket,"
            "object_key,file_name,media_type,byte_size,sha256,magic_type,scanner_result,"
            "verified_at) values(%s,%s,'legacy_bundle','pytest-conta',%s,'apostila.pdf',%s,"
            "1024,%s,%s,'legacy_trusted',clock_timestamp())",
            (
                self.loja,
                self.blob,
                f"conta/{self.rotulo}-{self.sufixo}.pdf",
                PDF,
                hashlib.sha256(f"blob-{self.rotulo}-{self.sufixo}".encode()).hexdigest(),
                PDF,
            ),
        )
        connection.execute(
            "insert into asset_record(seller_organization_id,id,catalog_product_id,title)"
            " values(%s,%s,%s,'Apostila')",
            (self.loja, self.asset, self.produto),
        )
        connection.execute(
            "insert into asset_version(seller_organization_id,asset_id,version,blob_id,"
            "actor_identity_user_id) values(%s,%s,1,%s,%s)",
            (self.loja, self.asset, self.blob, self.dona),
        )
        connection.execute(
            "insert into app_private.learning_pdf_study_state(seller_organization_id,"
            "buyer_identity_user_id,entitlement_id,asset_id,asset_version) "
            "values(%s,%s,%s,%s,1)",
            (self.loja, self.id, self.acesso, self.asset),
        )
        connection.execute(
            "insert into app_private.learning_pdf_bookmark(seller_organization_id,"
            "buyer_identity_user_id,entitlement_id,asset_id,asset_version,page_number) "
            "values(%s,%s,%s,%s,1,%s)",
            (self.loja, self.id, self.acesso, self.asset, self.pagina),
        )
        connection.execute(
            "insert into app_private.learning_pdf_annotation(seller_organization_id,"
            "buyer_identity_user_id,entitlement_id,asset_id,asset_version,id,page_number,"
            "tool,color,width,points) values(%s,%s,%s,%s,1,%s,%s,'pen','#FF0000',0.01,%s)",
            (
                self.loja,
                self.id,
                self.acesso,
                self.asset,
                uuid4(),
                self.pagina,
                Jsonb([[self.traco, self.traco]]),
            ),
        )
        connection.execute(
            "insert into app_private.email_outbox(seller_organization_id,kind,dedupe_key,"
            "recipient_identity_user_id,recipient_email,payload,status,sent_at) "
            "values(%s,'purchase_confirmed',%s,%s,%s,%s,'sent',clock_timestamp())",
            (
                self.loja,
                f"purchase:{uuid4().hex}",
                self.id,
                self.email,
                Jsonb({"schema_version": 1}),
            ),
        )
        # Histórico com metadata: dado que NÃO pode sair na exportação de
        # ninguém — nem da própria pessoa (metadata pode ter terceiro), nem
        # da outra.
        connection.execute(
            "insert into audit_event(seller_organization_id,actor_identity_user_id,action,"
            "target_type,target_id,result,request_id,metadata) values(%s,%s,'library.opened',"
            "'buyer_entitlement',%s,'succeeded',gen_random_uuid(),%s)",
            (self.loja, self.id, self.acesso, Jsonb({"segredo": f"segredo-{self.rotulo}"})),
        )


class Mundo:
    def __init__(self) -> None:
        sufixo = uuid4().hex[:10]
        self.ana = Pessoa("ana", sufixo, 0.4242)
        self.bia = Pessoa("bia", sufixo, 0.9797)

    def semear(self) -> None:
        assert ADMIN_URL
        with psycopg.connect(ADMIN_URL) as connection:
            self.ana.semear(connection)
            self.bia.semear(connection)
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


def test_a_exportacao_traz_cada_secao_so_do_proprio_sujeito(mundo: Mundo) -> None:
    # Modo de falha: uma junção sem o filtro do sujeito em QUALQUER das dez
    # subconsultas. Cada seção é conferida pelo conteúdo próprio, e o despejo
    # inteiro pelas marcas da outra pessoa.
    ana, bia = mundo.ana, mundo.bia
    exportado = exportar(ana.id, ana.id)
    assert exportado["schema_version"] == 1
    assert exportado["identity"] == {
        "email": ana.email,
        "email_verified": True,
        "display_name": ana.nome,
        "created_at": exportado["identity"]["created_at"],
    }
    assert exportado["memberships"] == []
    assert [(a["product"], a["store"], a["status"]) for a in exportado["accesses"]] == [
        (ana.titulo, ana.nome_da_loja, "active")
    ]
    assert [(p["product"], p["status"]) for p in exportado["purchases"]] == [(ana.titulo, "active")]
    (trilha,) = exportado["learning"]
    assert (trilha["product"], trilha["course"], trilha["progress_percent"]) == (
        ana.titulo,
        ana.nome_do_curso,
        100,
    )
    assert [(li["lesson"], li["progress_percent"]) for li in trilha["lessons"]] == [
        (ana.nome_da_aula, 100)
    ]
    assert [(b["product"], b["page_number"]) for b in exportado["pdf_bookmarks"]] == [
        (ana.titulo, ana.pagina)
    ]
    (anotacao,) = exportado["pdf_annotations"]
    assert anotacao["product"] == ana.titulo
    assert anotacao["points"] == [[ana.traco, ana.traco]]
    assert anotacao["width"] == 0.01
    assert [(e["kind"], e["status"]) for e in exportado["emails_sent"]] == [
        ("purchase_confirmed", "sent")
    ]
    assert "library.opened" in {e["action"] for e in exportado["activity"]}

    despejo = json.dumps(exportado, ensure_ascii=False)
    for marca in bia.marcas():
        assert marca not in despejo, marca
    assert "segredo-bia" not in despejo


def test_a_exportacao_nao_carrega_segredo_nem_id_interno(mundo: Mundo) -> None:
    # Modo de falha: "select *" numa das tabelas, ou metadata copiada. Os nomes
    # abaixo são os campos que nunca podem sair — asserção textual sobre o
    # despejo inteiro.
    ana = mundo.ana
    despejo = json.dumps(exportar(ana.id, ana.id), ensure_ascii=False)
    for proibido in (
        "identity_sub",
        "identity_issuer",
        "sha256",
        "metadata",
        "request_id",
        "target_id",
        "segredo",
        ana.compra_sha256,
        str(ana.id),
        str(ana.loja),
        str(ana.produto),
        str(ana.acesso),
        str(ana.asset),
        ISSUER,
    ):
        assert proibido not in despejo, proibido


def test_a_dona_da_loja_exporta_a_propria_membership_e_nada_da_compradora(
    mundo: Mundo,
) -> None:
    ana = mundo.ana
    exportado = exportar(ana.dona, ana.dona)
    assert [(m["store"], m["role"], m["status"]) for m in exportado["memberships"]] == [
        (ana.nome_da_loja, "seller_owner", "active")
    ]
    assert exportado["accesses"] == [] and exportado["purchases"] == []
    despejo = json.dumps(exportado, ensure_ascii=False)
    for marca in (ana.email, ana.nome, ana.nome_da_aula, str(ana.traco)):
        assert marca not in despejo, marca


def test_a_exportacao_grava_exatamente_um_audit_com_o_proprio_ator(mundo: Mundo) -> None:
    ana = mundo.ana
    antes = len(audits(ana.id, "account.data.exported"))
    exportar(ana.id, ana.id)
    registros = audits(ana.id, "account.data.exported")
    assert len(registros) == antes + 1
    ultimo = registros[-1]
    assert ultimo["seller"] is None
    assert ultimo["result"] == "succeeded"
    assert ultimo["target_type"] == "identity_user"
    assert ultimo["target_id"] == ana.id
    # E a própria exportação aparece na atividade da pessoa da próxima vez.
    acoes = {item["action"] for item in exportar(ana.id, ana.id)["activity"]}
    assert "account.data.exported" in acoes


@pytest.mark.parametrize(
    ("descricao", "montar"),
    [
        ("pedir os dados de outra pessoa", lambda m: dict(actor=m.ana.id, alvo=m.bia.id)),
        (
            "com loja no contexto",
            lambda m: dict(actor=m.ana.id, alvo=m.ana.id, seller=m.ana.loja),
        ),
        (
            "com a capability da biblioteca",
            lambda m: dict(actor=m.ana.id, alvo=m.ana.id, capability="library.read"),
        ),
        (
            "a dona da loja tentando ler a compradora",
            lambda m: dict(actor=m.ana.dona, alvo=m.ana.id),
        ),
    ],
)
def test_a_exportacao_recusa_qualquer_contexto_que_nao_seja_o_proprio(
    mundo: Mundo, descricao: str, montar: Any
) -> None:
    # Modo de falha: a guarda confia no parâmetro em vez do contexto assinado.
    # Cada caso é uma forma de pedir dado que não é seu. Todos: 42501, e
    # nenhum audit de exportação para a vítima.
    antes = len(audits(mundo.bia.id, "account.data.exported"))
    with pytest.raises(InsufficientPrivilege):
        exportar(**montar(mundo))
    assert len(audits(mundo.bia.id, "account.data.exported")) == antes, descricao


def test_o_runtime_nao_grava_audit_de_comprador_sem_a_funcao(mundo: Mundo) -> None:
    # A premissa da migration: sem SECURITY DEFINER o comprador puro não
    # escreve auditoria — a política exige membership. Se isto um dia passar,
    # a função deixou de ser necessária e a matriz precisa ser revista.
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, mundo.ana.id, None, "audit.write")
        with pytest.raises(InsufficientPrivilege):
            connection.execute(
                "insert into audit_event(seller_organization_id,actor_identity_user_id,action,"
                "target_type,result,request_id) values(null,%s,'account.data.exported',"
                "'identity_user','succeeded',gen_random_uuid())",
                (mundo.ana.id,),
            )


def test_as_funcoes_sao_security_definer_com_search_path_e_sem_execute_publico() -> None:
    # Modo de falha: alguém recria a função sem SECURITY DEFINER (o runtime
    # passa a bater na RLS e a auditoria some) ou dá EXECUTE a PUBLIC.
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        rows = connection.execute(
            """
            select p.proname, p.prosecdef,
                   coalesce(array_to_string(p.proconfig,','),'') like '%%search_path=%%',
                   has_function_privilege('appsell_runtime',p.oid,'execute'),
                   exists(select 1 from aclexplode(p.proacl) a where a.grantee=0)
            from pg_proc p join pg_namespace n on n.oid=p.pronamespace
            where n.nspname='app_private' and p.proname in
              ('export_account_data','request_account_deletion','read_account_status')
            order by p.proname
            """
        ).fetchall()
    assert [row[0] for row in rows] == [
        "export_account_data",
        "read_account_status",
        "request_account_deletion",
    ]
    for nome, definer, com_search_path, runtime_executa, publico_executa in rows:
        assert definer, nome
        assert com_search_path, nome
        assert runtime_executa, nome
        assert not publico_executa, nome


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


def concluir_exclusao(operador: UUID, pessoa: UUID) -> None:
    """O que o runbook manda: o OPERADOR é o ator, a pessoa é o alvo."""
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "insert into audit_event(seller_organization_id,actor_identity_user_id,action,"
            "target_type,target_id,result,request_id) values(null,%s,"
            "'account.deletion.completed','identity_user',%s,'succeeded',gen_random_uuid())",
            (operador, pessoa),
        )
        connection.commit()


def test_o_pedido_de_exclusao_registra_e_nao_apaga(mundo: Mundo) -> None:
    # Decisão 019: apagar a identidade destrói a prova da compra. O pedido é
    # um audit com o próprio usuário como ator; a conta continua inteira.
    bia = mundo.bia
    assert ler_status(bia.id, bia.id) is None
    quando, repetido = pedir_exclusao(bia.id, bia.id)
    assert repetido is False
    registros = audits(bia.id, "account.deletion.requested")
    assert len(registros) == 1
    assert registros[0]["seller"] is None
    assert registros[0]["target_id"] == bia.id
    assert ler_status(bia.id, bia.id) == quando
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        ainda = connection.execute(
            "select count(*) from identity_user where id=%s", (bia.id,)
        ).fetchone()
        acessos = connection.execute(
            "select count(*) from buyer_entitlement where buyer_identity_user_id=%s", (bia.id,)
        ).fetchone()
    assert ainda == (1,) and acessos == (1,)


def test_o_pedido_repetido_devolve_o_mesmo_enquanto_estiver_aberto(mundo: Mundo) -> None:
    # Modo de falha: um clique por pedido. O segundo devolve a data do
    # primeiro e não grava nada.
    bia = mundo.bia
    primeiro, _ = pedir_exclusao(bia.id, bia.id)
    segundo, repetido = pedir_exclusao(bia.id, bia.id)
    assert repetido is True and segundo == primeiro
    assert len(audits(bia.id, "account.deletion.requested")) == 1


def test_o_pedido_e_o_status_recusam_quem_nao_e_o_sujeito(mundo: Mundo) -> None:
    ana, bia = mundo.ana, mundo.bia
    with pytest.raises(InsufficientPrivilege):
        pedir_exclusao(ana.id, bia.id)
    with pytest.raises(InsufficientPrivilege):
        pedir_exclusao(bia.id, bia.id, capability="account.export")
    # O status é SQL puro: fora do contexto certo devolve vazio, nunca a data
    # da outra pessoa.
    assert ler_status(ana.id, bia.id) is None
    assert ler_status(bia.id, bia.id, capability="account.export") is None
    assert len(audits(bia.id, "account.deletion.requested")) == 1


def test_um_pedido_antigo_nao_expira(mundo: Mundo) -> None:
    # Modo de falha (achado da revisão): uma janela por idade faria um pedido
    # não atendido "sumir" da tela e a pessoa gerar um segundo em cima dele.
    ana = mundo.ana
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        antigo = connection.execute(
            "insert into audit_event(seller_organization_id,actor_identity_user_id,action,"
            "target_type,target_id,result,request_id,created_at) values(null,%s,"
            "'account.deletion.requested','identity_user',%s,'succeeded',gen_random_uuid(),"
            "clock_timestamp()-interval '400 days') returning created_at",
            (ana.id, ana.id),
        ).fetchone()
        connection.commit()
    assert antigo is not None
    assert ler_status(ana.id, ana.id) == antigo[0]
    quando, repetido = pedir_exclusao(ana.id, ana.id)
    assert repetido is True and quando == antigo[0]
    assert len(audits(ana.id, "account.deletion.requested")) == 1


def test_a_conclusao_pelo_operador_fecha_o_pedido_e_permite_um_novo(mundo: Mundo) -> None:
    # O operador (a dona da loja A faz as vezes do superadmin aqui) registra a
    # conclusão com a PESSOA como alvo. Só isso fecha o pedido — e um pedido
    # novo depois disso é um pedido novo, com data nova.
    bia, operador = mundo.bia, mundo.ana.dona
    aberto = ler_status(bia.id, bia.id)
    assert aberto is not None
    concluir_exclusao(operador, bia.id)
    assert ler_status(bia.id, bia.id) is None
    # A conclusão de OUTRA pessoa não fecha o pedido da Ana.
    assert ler_status(mundo.ana.id, mundo.ana.id) is not None
    novo, repetido = pedir_exclusao(bia.id, bia.id)
    assert repetido is False and novo > aberto
    assert len(audits(bia.id, "account.deletion.requested")) == 2
    # E o registro da conclusão diz quem fez: o operador, não a pessoa.
    assert audits(bia.id, "account.deletion.completed") == []
    assert [r["target_id"] for r in audits(operador, "account.deletion.completed")] == [bia.id]


def test_pedidos_simultaneos_viram_um_so(mundo: Mundo) -> None:
    # Modo de falha: dois cliques (ou duas abas) na mesma fração de segundo.
    # A trava por pessoa serializa; só um insere, os outros leem o dele.
    assert ADMIN_URL
    caio = uuid4()
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "insert into identity_user(id,identity_issuer,identity_sub,email_normalized,"
            "email_verified) values(%s,%s,%s,%s,true)",
            (caio, ISSUER, f"caio-{caio.hex[:8]}", f"caio-{caio.hex[:8]}@example.test"),
        )
        connection.commit()
    with ThreadPoolExecutor(max_workers=8) as pool:
        resultados = list(pool.map(lambda _: pedir_exclusao(caio, caio), range(8)))
    datas = {quando for quando, _ in resultados}
    assert len(datas) == 1
    assert sum(1 for _, repetido in resultados if not repetido) == 1
    assert len(audits(caio, "account.deletion.requested")) == 1
