"""Migration 0052 — configurações da loja, logo, promessa e a vitrine com marca (#352).

O que este arquivo prova, e por quê:

- **papéis**: dono e admin leem e escrevem; editor, suporte e estranho recebem
  `42501` em toda função — é o critério de aceite que a issue chama de "teste
  negativo por papel";
- **forjas**: capability trocada e loja trocada no contexto também dão `42501`;
- **slug imutável**: nenhuma função recebe slug, e o runtime não consegue
  alterá-lo por UPDATE direto;
- **logo**: a chave é a reservada pelo intent, o tipo é o declarado, um upload
  fechado não grava, e apagar duas vezes é idempotente — o mesmo contrato da
  capa (0037), com a loja como dona;
- **vitrine**: os campos novos SÓ aparecem quando existem (é o que mantém o
  web antigo de pé entre os deploys), e o currículo vem do snapshot publicado;
- **área logada**: comprador de duas lojas, ambas com marca, não vê logo nem
  cor em nenhuma projeção da biblioteca.

O mundo é próprio e recriado a cada execução: duas lojas, quatro papéis na
primeira, um produto publicado em cada, e um comprador com acesso nos dois.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.environ.get("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY_HEX = os.environ.get("APPSELL_TEST_CONTEXT_KEY_HEX")

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY_HEX)),
    reason="isolated PostgreSQL environment is required",
)

ISSUER = "https://identity.loja.test"
OWNER = UUID("52000000-0000-4000-8000-000000000001")
ADMIN = UUID("52000000-0000-4000-8000-000000000002")
EDITOR = UUID("52000000-0000-4000-8000-000000000003")
SUPPORT = UUID("52000000-0000-4000-8000-000000000004")
STRANGER = UUID("52000000-0000-4000-8000-000000000005")
BUYER = UUID("52000000-0000-4000-8000-000000000006")
LOJA = UUID("52000000-0000-4000-8000-000000000101")
OUTRA_LOJA = UUID("52000000-0000-4000-8000-000000000102")
PRODUTO = UUID("52000000-0000-4000-8000-000000000201")
PUBLICADO = UUID("52000000-0000-4000-8000-000000000202")
PUBLICADO_B = UUID("52000000-0000-4000-8000-000000000203")
PUBLICACAO = UUID("52000000-0000-4000-8000-000000000301")
PUBLICACAO_B = UUID("52000000-0000-4000-8000-000000000302")
ACESSO = UUID("52000000-0000-4000-8000-000000000401")
ACESSO_B = UUID("52000000-0000-4000-8000-000000000402")

READ = "seller.settings.read"
UPDATE = "seller.settings.update"
DRAFT = "catalog.draft.update"
QUARANTINE = "appsell-quarantine"
READY = "appsell-ready"
FORBIDDEN = "42501"

FUNCOES_DO_RUNTIME = (
    "app_private.read_seller_settings(uuid,uuid)",
    "app_private.update_seller_settings(uuid,uuid,text,text,text,text,uuid)",
    "app_private.create_seller_logo_upload_intent(uuid,uuid,text,bigint,text,text,text,uuid,text,uuid)",
    "app_private.read_seller_logo_upload(uuid,uuid,uuid)",
    "app_private.close_seller_logo_upload(uuid,uuid,uuid,text,text,uuid)",
    "app_private.set_seller_logo(uuid,uuid,uuid,text,text,text,text,integer,integer,bigint,uuid,text,uuid)",
    "app_private.clear_seller_logo(uuid,uuid,uuid,text,uuid)",
    "app_private.update_product_headline(uuid,uuid,uuid,text,bigint,uuid,text,uuid)",
)
FUNCOES_AUXILIARES = (
    "app_private.seller_settings_projection(public.seller_organization)",
    "app_private.published_curriculum_summary(jsonb)",
)


def digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def sign(actor: UUID, seller: UUID | None, capability: str, issued: int, expires: int) -> str:
    payload = f"{actor}:{seller or ''}:{capability}:{issued}:{expires}"
    return hmac.new(
        bytes.fromhex(CONTEXT_KEY_HEX or ""), payload.encode(), hashlib.sha256
    ).hexdigest()


def contexto(
    connection: psycopg.Connection[Any], actor: UUID, seller: UUID | None, capability: str
) -> None:
    issued = int(time.time())
    expires = issued + 60
    connection.execute(
        "select app_private.apply_request_context(%s,%s,%s,%s,%s,%s)",
        (
            actor,
            seller,
            capability,
            issued,
            expires,
            sign(actor, seller, capability, issued, expires),
        ),
    )


def sqlstate_de(error: BaseException) -> str:
    return str(getattr(error, "sqlstate", "") or "")


def snapshot(
    seller: UUID,
    product: UUID,
    slug: str,
    title: str,
    *,
    course: UUID,
    module: UUID,
    aulas: list[tuple[UUID, str, int]],
) -> dict[str, Any]:
    """Um snapshot v2 (sem materiais) com um curso, um módulo e as aulas pedidas.

    Materiais não entram: a projeção da publicação confere cada um contra
    asset_record, e o que se prova aqui é o currículo, não o armazenamento. A
    contagem de materiais é provada chamando published_curriculum_summary direto.
    """
    return {
        "schema_version": 2,
        "seller_organization_id": str(seller),
        "product": {"id": str(product), "slug": slug, "title": title, "description": "Publicado."},
        "offer": {
            "id": str(uuid4()),
            "title": "Oferta",
            "summary": "",
            "billing_authority": "external_unconfigured",
        },
        "courses": [
            {
                "id": str(course),
                "title": "Curso",
                "description": "",
                "position": 0,
                "modules": [
                    {
                        "id": str(module),
                        "title": "Módulo",
                        "status": "active",
                        "position": 0,
                        "lessons": [
                            {
                                "id": str(lesson),
                                "title": titulo,
                                "kind": "text",
                                "content": "CONTEUDO SECRETO DA AULA",
                                "status": "active",
                                "position": posicao,
                            }
                            for posicao, (lesson, titulo, _materiais) in enumerate(aulas)
                        ],
                    }
                ],
            }
        ],
    }


def publicar(
    connection: psycopg.Connection[Any],
    *,
    seller: UUID,
    product: UUID,
    publication: UUID,
    slug: str,
    title: str,
    aulas: list[tuple[str, int]],
) -> None:
    connection.execute(
        "insert into catalog_product(seller_organization_id,id,slug,title,description,status) "
        "values(%s,%s,%s,%s,'Publicado.','draft')",
        (seller, product, slug, title),
    )
    connection.execute(
        "insert into offer(seller_organization_id,catalog_product_id,title,summary,status) "
        "values(%s,%s,'Oferta','','draft')",
        (seller, product),
    )
    # O gatilho de publicação projeta os cursos do snapshot em publication_course,
    # com FK para course: as linhas de curso, módulo e aula precisam existir com
    # os mesmos ids que o snapshot carrega.
    course = uuid4()
    module = uuid4()
    aulas_com_id = [(uuid4(), titulo, materiais) for titulo, materiais in aulas]
    connection.execute(
        "insert into course(seller_organization_id,id,title,description,status) "
        "values(%s,%s,'Curso','','draft')",
        (seller, course),
    )
    connection.execute(
        "insert into product_course(seller_organization_id,catalog_product_id,course_id,position) "
        "values(%s,%s,%s,0)",
        (seller, product, course),
    )
    connection.execute(
        "insert into course_module(seller_organization_id,id,course_id,title,status,position) "
        "values(%s,%s,%s,'Módulo','active',0)",
        (seller, module, course),
    )
    for posicao, (lesson, titulo, _materiais) in enumerate(aulas_com_id):
        connection.execute(
            "insert into lesson(seller_organization_id,id,course_id,module_id,title,body_text,"
            "status,position) values(%s,%s,%s,%s,%s,'Conteúdo','active',%s)",
            (seller, lesson, course, module, titulo, posicao),
        )
    conteudo = snapshot(
        seller, product, slug, title, course=course, module=module, aulas=aulas_com_id
    )
    connection.execute(
        """
        with payload as (select %s::jsonb snapshot)
        insert into publication(
          seller_organization_id,id,catalog_product_id,version,schema_version,
          source_sha256,snapshot,actor_identity_user_id
        ) select %s,%s,%s,1,2,
          encode(digest(convert_to(snapshot::text,'UTF8'),'sha256'),'hex'),snapshot,%s
        from payload
        """,
        (Jsonb(conteudo), seller, publication, product, OWNER),
    )
    connection.execute(
        "update catalog_product set status='published',published_snapshot_id=%s "
        "where seller_organization_id=%s and id=%s",
        (publication, seller, product),
    )
    connection.execute(
        "update course set status='published' where seller_organization_id=%s and id=%s",
        (seller, course),
    )


@pytest.fixture(scope="module", autouse=True)
def mundo() -> None:
    """Cria o mundo uma vez; nas execuções seguintes só repõe o estado mutável.

    Publicações e auditoria são append-only por gatilho, então nada disso pode
    ser apagado e recriado num banco reaproveitado. O que muda entre testes
    (papéis, marca, promessa, uploads de logo) é reposto aqui.
    """
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        for identity, sub in (
            (OWNER, "loja-dona"),
            (ADMIN, "loja-admin"),
            (EDITOR, "loja-editora"),
            (SUPPORT, "loja-suporte"),
            (STRANGER, "loja-estranho"),
            (BUYER, "loja-compradora"),
        ):
            connection.execute(
                "insert into identity_user(id,identity_issuer,identity_sub,email_normalized,"
                "email_verified) values(%s,%s,%s,%s,true) on conflict (id) do nothing",
                (identity, ISSUER, sub, f"{sub}@loja.example.test"),
            )
        connection.execute(
            "insert into seller_organization(id,slug,display_name,status) "
            "values(%s,'loja-de-teste','Loja de Teste','active'),"
            "(%s,'outra-loja','Outra Loja','active') "
            "on conflict (id) do update set display_name=excluded.display_name, status='active'",
            (LOJA, OUTRA_LOJA),
        )
        for loja in (LOJA, OUTRA_LOJA):
            marcar(connection, loja, com_marca=False)
        connection.execute(
            "delete from seller_membership where seller_organization_id = any(%s::uuid[])",
            ([LOJA, OUTRA_LOJA],),
        )
        for identity, role in (
            (OWNER, "seller_owner"),
            (ADMIN, "seller_admin"),
            (EDITOR, "seller_editor"),
            (SUPPORT, "seller_support"),
        ):
            connection.execute(
                "insert into seller_membership(seller_organization_id,identity_user_id,"
                "role,status) values(%s,%s,%s,'active')",
                (LOJA, identity, role),
            )
        connection.execute(
            "insert into seller_membership(seller_organization_id,identity_user_id,role,status) "
            "values(%s,%s,'seller_owner','active')",
            (OUTRA_LOJA, STRANGER),
        )
        connection.execute(
            "delete from app_private.seller_logo_upload where seller_organization_id=%s", (LOJA,)
        )
        connection.execute(
            "insert into catalog_product(seller_organization_id,id,slug,title,description,status) "
            "values(%s,%s,'produto-de-teste','Produto de Teste','Descrição','draft') "
            "on conflict do nothing",
            (LOJA, PRODUTO),
        )
        connection.execute(
            "update catalog_product set headline=null where seller_organization_id "
            "= any(%s::uuid[])",
            ([LOJA, OUTRA_LOJA],),
        )
        ja_publicado = connection.execute(
            "select count(*) from catalog_product where id=%s", (PUBLICADO,)
        ).fetchone()
        if ja_publicado == (0,):
            publicar(
                connection,
                seller=LOJA,
                product=PUBLICADO,
                publication=PUBLICACAO,
                slug="curso-publicado",
                title="Curso Publicado",
                aulas=[("Prospecção com IA", 2), ("Cotação em três prompts", 1)],
            )
            publicar(
                connection,
                seller=OUTRA_LOJA,
                product=PUBLICADO_B,
                publication=PUBLICACAO_B,
                slug="curso-da-outra",
                title="Curso da Outra",
                aulas=[("Aula única", 0)],
            )
            for seller, product, publication, entitlement, key in (
                (LOJA, PUBLICADO, PUBLICACAO, ACESSO, "loja-compradora-a"),
                (OUTRA_LOJA, PUBLICADO_B, PUBLICACAO_B, ACESSO_B, "loja-compradora-b"),
            ):
                connection.execute(
                    "insert into buyer_entitlement(seller_organization_id,id,"
                    "buyer_identity_user_id,catalog_product_id,publication_id,reference_key,"
                    "source,status,progress_percent) values(%s,%s,%s,%s,%s,%s,'manual','active',0)",
                    (seller, entitlement, BUYER, product, publication, key),
                )
        connection.commit()


def ler(
    actor: UUID,
    seller: UUID = LOJA,
    *,
    capability: str = READ,
    contexto_seller: UUID | None = None,
) -> dict[str, Any]:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        contexto(connection, actor, contexto_seller or seller, capability)
        row = connection.execute(
            "select app_private.read_seller_settings(%s,%s)", (actor, seller)
        ).fetchone()
    assert row is not None
    return cast(dict[str, Any], row[0])


def atualizar(
    actor: UUID,
    *,
    display_name: str = "Loja de Teste",
    tagline: str | None = None,
    about: str | None = None,
    accent_color: str | None = None,
    seller: UUID = LOJA,
) -> dict[str, Any]:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        contexto(connection, actor, seller, UPDATE)
        row = connection.execute(
            "select app_private.update_seller_settings(%s,%s,%s,%s,%s,%s,%s)",
            (actor, seller, display_name, tagline, about, accent_color, uuid4()),
        ).fetchone()
        connection.commit()
    assert row is not None
    return cast(dict[str, Any], row[0])


def intent(
    actor: UUID = OWNER, *, media_type: str = "image/png", seller: UUID = LOJA
) -> dict[str, Any]:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        contexto(connection, actor, seller, UPDATE)
        row = connection.execute(
            "select app_private.create_seller_logo_upload_intent(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                actor,
                seller,
                media_type,
                4096,
                digest("logo"),
                QUARANTINE,
                READY,
                uuid4(),
                digest("body"),
                uuid4(),
            ),
        ).fetchone()
        connection.commit()
    assert row is not None
    return cast(dict[str, Any], row[0])


def gravar_logo(
    upload: dict[str, Any],
    *,
    actor: UUID = OWNER,
    object_key: str | None = None,
    media_type: str = "image/png",
) -> tuple[Any, ...]:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        contexto(connection, actor, LOJA, UPDATE)
        row = connection.execute(
            "select * from app_private.set_seller_logo(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                actor,
                LOJA,
                UUID(upload["upload_id"]),
                object_key or upload["ready_key"],
                "storage-version-1",
                media_type,
                digest("sanitised"),
                320,
                320,
                4096,
                uuid4(),
                digest("body"),
                uuid4(),
            ),
        ).fetchone()
        connection.commit()
    assert row is not None
    return row


def apagar_logo(actor: UUID = OWNER) -> tuple[Any, ...]:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        contexto(connection, actor, LOJA, UPDATE)
        row = connection.execute(
            "select * from app_private.clear_seller_logo(%s,%s,%s,%s,%s)",
            (actor, LOJA, uuid4(), digest("body"), uuid4()),
        ).fetchone()
        connection.commit()
    assert row is not None
    return row


def versao_do_produto(product: UUID = PRODUTO, seller: UUID = LOJA) -> int:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select version from catalog_product where seller_organization_id=%s and id=%s",
            (seller, product),
        ).fetchone()
    assert row is not None
    return int(row[0])


def promessa(
    actor: UUID, headline: str | None, expected_version: int, *, product: UUID = PRODUTO
) -> tuple[Any, ...]:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        contexto(connection, actor, LOJA, DRAFT)
        row = connection.execute(
            "select * from app_private.update_product_headline(%s,%s,%s,%s,%s,%s,%s,%s)",
            (actor, LOJA, product, headline, expected_version, uuid4(), digest("body"), uuid4()),
        ).fetchone()
        connection.commit()
    assert row is not None
    return row


def eventos(action: str, seller: UUID = LOJA) -> list[dict[str, Any]]:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        rows = connection.execute(
            "select actor_identity_user_id,target_type,target_id,result,metadata "
            "from audit_event where seller_organization_id=%s and action=%s order by created_at",
            (seller, action),
        ).fetchall()
    return [
        {
            "actor": row[0],
            "target_type": row[1],
            "target_id": row[2],
            "result": row[3],
            "metadata": row[4],
        }
        for row in rows
    ]


def vitrine(slug: str) -> dict[str, Any]:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        row = connection.execute(
            "select app_private.read_public_storefront(%s)", (slug,)
        ).fetchone()
    assert row is not None
    return cast(dict[str, Any], row[0])


def marcar(connection: psycopg.Connection[Any], seller: UUID, *, com_marca: bool) -> None:
    if com_marca:
        connection.execute(
            "update seller_organization set tagline='IA para corretores', about='Sobre nós', "
            "accent_color='#1a6b52', logo_object_key=%s, logo_storage_version_id='v1', "
            "logo_media_type='image/png', logo_sha256=%s, logo_width=300, logo_height=300, "
            "logo_byte_size=4096, logo_updated_at=clock_timestamp() where id=%s",
            (f"tenants/{seller}/logo/{uuid4()}", digest("logo"), seller),
        )
    else:
        connection.execute(
            "update seller_organization set tagline=null, about=null, accent_color=null, "
            "logo_object_key=null, logo_storage_version_id=null, logo_media_type=null, "
            "logo_sha256=null, logo_width=null, logo_height=null, logo_byte_size=null, "
            "logo_updated_at=null where id=%s",
            (seller,),
        )


# ------------------------------------------------------------ propriedades


def test_schema_ready_e_os_donos_das_funcoes() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        assert connection.execute("select app_private.schema_ready()").fetchone() == (True,)
        for signature in FUNCOES_DO_RUNTIME + FUNCOES_AUXILIARES:
            row = connection.execute(
                "select r.rolname from pg_proc p "
                "join pg_roles r on r.oid=p.proowner where p.oid=%s::regprocedure",
                (signature,),
            ).fetchone()
            assert row == ("appsell_owner",), signature
        for signature in FUNCOES_DO_RUNTIME:
            row = connection.execute(
                "select p.prosecdef, p.proconfig, "
                "has_function_privilege('appsell_runtime',%s,'EXECUTE') from pg_proc p "
                "where p.oid=%s::regprocedure",
                (signature, signature),
            ).fetchone()
            assert row is not None
            assert row[0] is True, signature
            assert any(item.startswith("search_path=pg_catalog") for item in row[1]), signature
            assert row[2] is True, signature
        for signature in FUNCOES_AUXILIARES:
            row = connection.execute(
                "select has_function_privilege('appsell_runtime',%s,'EXECUTE')", (signature,)
            ).fetchone()
            assert row == (False,), signature


def test_toda_funcao_de_app_private_pertence_ao_appsell_owner() -> None:
    # Teste de classe (0053). Uma definer do appsell_owner que chama uma função
    # de outro dono cai em 42501 — foi assim que Configurações → Loja respondeu
    # 403 ao próprio dono em produção, porque a 0025 nasceu com dono = migrador
    # lá (e não aqui). Dono igual em todo ambiente é o que fecha a classe.
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        alheias = connection.execute(
            "select p.proname, r.rolname from pg_proc p "
            "join pg_roles r on r.oid=p.proowner "
            "join pg_namespace n on n.oid=p.pronamespace "
            "where n.nspname='app_private' and r.rolname <> 'appsell_owner' order by 1"
        ).fetchall()
        assert alheias == []
        assert connection.execute(
            "select has_function_privilege('appsell_owner',"
            "'app_private.resolve_seller_membership_role(uuid)','EXECUTE')"
        ).fetchone() == (True,)


def test_nenhuma_funcao_recebe_slug_e_o_runtime_nao_o_altera() -> None:
    # Slug imutável na v1: não há parâmetro para ele em função nenhuma, e o
    # runtime não tem UPDATE na tabela — nem por policy, nem por GRANT.
    assert ADMIN_URL and RUNTIME_URL
    with psycopg.connect(ADMIN_URL) as connection:
        for signature in FUNCOES_DO_RUNTIME:
            row = connection.execute(
                "select pg_get_function_arguments(%s::regprocedure)", (signature,)
            ).fetchone()
            assert row is not None
            assert "slug" not in row[0].lower(), signature
    with psycopg.connect(RUNTIME_URL) as connection:
        contexto(connection, OWNER, LOJA, UPDATE)
        with pytest.raises(psycopg.Error) as excinfo:
            connection.execute(
                "update seller_organization set slug='loja-renomeada' where id=%s", (LOJA,)
            )
        # Nem policy nem GRANT: o runtime não tem UPDATE na tabela.
        assert sqlstate_de(excinfo.value) == FORBIDDEN
        connection.rollback()
    with psycopg.connect(ADMIN_URL) as connection:
        assert connection.execute(
            "select slug from seller_organization where id=%s", (LOJA,)
        ).fetchone() == ("loja-de-teste",)


# ------------------------------------------------------------------ papéis


def test_dono_e_admin_leem_editor_suporte_e_estranho_nao() -> None:
    assert ler(OWNER)["slug"] == "loja-de-teste"
    assert ler(ADMIN)["display_name"] == "Loja de Teste"
    for actor in (EDITOR, SUPPORT, STRANGER):
        with pytest.raises(psycopg.Error) as excinfo:
            ler(actor)
        assert sqlstate_de(excinfo.value) == FORBIDDEN, actor


def test_editor_e_suporte_nao_alteram_nome_logo_nem_cor() -> None:
    for actor in (EDITOR, SUPPORT, STRANGER):
        with pytest.raises(psycopg.Error) as excinfo:
            atualizar(actor, display_name="Golpe")
        assert sqlstate_de(excinfo.value) == FORBIDDEN, actor
        with pytest.raises(psycopg.Error) as excinfo:
            intent(actor)
        assert sqlstate_de(excinfo.value) == FORBIDDEN, actor
        with pytest.raises(psycopg.Error) as excinfo:
            apagar_logo(actor)
        assert sqlstate_de(excinfo.value) == FORBIDDEN, actor
    assert ler(OWNER)["display_name"] == "Loja de Teste"


def test_forjas_de_capability_e_de_loja_sao_recusadas() -> None:
    # Capability certa, loja errada no contexto: a função da 0025 recusa antes
    # de olhar a membership. Capability errada com tudo o mais certo: recusa
    # aqui mesmo. Nenhum dos dois caminhos chega a ler a loja.
    with pytest.raises(psycopg.Error) as excinfo:
        ler(OWNER, contexto_seller=OUTRA_LOJA)
    assert sqlstate_de(excinfo.value) == FORBIDDEN
    with pytest.raises(psycopg.Error) as excinfo:
        ler(OWNER, capability=UPDATE)
    assert sqlstate_de(excinfo.value) == FORBIDDEN
    # O dono da outra loja não vê esta, mesmo com o contexto certo para a dele.
    with pytest.raises(psycopg.Error) as excinfo:
        ler(STRANGER, seller=LOJA, contexto_seller=OUTRA_LOJA)
    assert sqlstate_de(excinfo.value) == FORBIDDEN


# ---------------------------------------------------------------- escrita


def test_atualizar_grava_campos_e_auditoria_com_antes_e_depois() -> None:
    depois = atualizar(
        OWNER,
        display_name="Loja Renomeada",
        tagline="Cursos para quem vende",
        about="Doze anos apoiando corretoras.",
        accent_color="#1a6b52",
    )
    assert depois["changed"] is True
    assert depois["display_name"] == "Loja Renomeada"
    assert depois["accent_color"] == "#1a6b52"
    assert depois["logo"] is None
    registros = eventos("seller.settings.updated")
    assert registros[-1]["actor"] == OWNER
    assert registros[-1]["target_type"] == "seller_organization"
    assert registros[-1]["target_id"] == LOJA
    assert registros[-1]["result"] == "succeeded"
    metadata = registros[-1]["metadata"]
    assert set(metadata["changed"]) == {"display_name", "tagline", "about", "accent_color"}
    assert metadata["display_name_before"] == "Loja de Teste"
    assert metadata["display_name_after"] == "Loja Renomeada"
    # Repetir sem mudar nada não grava auditoria nova.
    total = len(registros)
    igual = atualizar(
        ADMIN,
        display_name="Loja Renomeada",
        tagline="Cursos para quem vende",
        about="Doze anos apoiando corretoras.",
        accent_color="#1a6b52",
    )
    assert igual["changed"] is False
    assert len(eventos("seller.settings.updated")) == total
    # Volta ao nome original para os outros testes.
    atualizar(OWNER, display_name="Loja de Teste", tagline=None, about=None, accent_color="#1a6b52")


@pytest.mark.parametrize(
    "campos",
    [
        {"accent_color": "#FFFFFF"},
        {"accent_color": "1a6b52"},
        {"accent_color": "#12345"},
        {"display_name": " com espaço"},
        {"display_name": "x"},
        {"tagline": "a"},
        {"about": "com\ncontrole"},
    ],
)
def test_formato_invalido_e_22023(campos: dict[str, str]) -> None:
    with pytest.raises(psycopg.Error) as excinfo:
        atualizar(
            OWNER,
            display_name=campos.get("display_name", "Loja de Teste"),
            tagline=campos.get("tagline"),
            about=campos.get("about"),
            accent_color=campos.get("accent_color"),
        )
    assert sqlstate_de(excinfo.value) == "22023"


def test_limites_sao_em_caracteres_nao_em_bytes() -> None:
    # A tela promete 160 caracteres; "á" tem dois bytes. Uma frase em português
    # no limite tem de entrar.
    depois = atualizar(OWNER, tagline="á" * 160, about="ç" * 600)
    assert depois["tagline"] == "á" * 160
    atualizar(OWNER, tagline=None, about=None, accent_color="#1a6b52")
    versao = versao_do_produto()
    promessa(OWNER, "é" * 200, versao)
    promessa(OWNER, None, versao + 1)


# ------------------------------------------------------------------- logo


def test_logo_intent_reserva_chave_por_loja_e_substitui_a_anterior() -> None:
    primeiro = intent()
    assert primeiro["quarantine_key"] == f"tenants/{LOJA}/quarantine/logo"
    assert primeiro["ready_key"] == f"tenants/{LOJA}/logo/{primeiro['upload_id']}"
    segundo = intent()
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        rows: dict[str, str] = dict(
            connection.execute(
                "select id::text,status from app_private.seller_logo_upload "
                "where seller_organization_id=%s and id = any(%s::uuid[])",
                (LOJA, [primeiro["upload_id"], segundo["upload_id"]]),
            ).fetchall()
        )
    assert rows[primeiro["upload_id"]] == "cancelled"
    assert rows[segundo["upload_id"]] == "pending"


def test_logo_so_grava_na_chave_reservada_com_o_tipo_declarado_e_uma_vez() -> None:
    upload = intent(media_type="image/png")
    with pytest.raises(psycopg.Error) as excinfo:
        gravar_logo(upload, object_key=f"tenants/{LOJA}/logo/outra-chave")
    assert sqlstate_de(excinfo.value) == "28000"
    with pytest.raises(psycopg.Error) as excinfo:
        gravar_logo(upload, media_type="image/jpeg")
    assert sqlstate_de(excinfo.value) == "22023"
    (anterior,) = gravar_logo(upload)
    assert anterior is None
    assert ler(OWNER)["logo"]["object_key"] == upload["ready_key"]
    with pytest.raises(psycopg.Error) as excinfo:
        gravar_logo(upload)
    assert sqlstate_de(excinfo.value) == "P4229"
    # Trocar o logo devolve a chave anterior para a API apagar do bucket.
    novo = intent()
    (anterior,) = gravar_logo(novo)
    assert anterior == upload["ready_key"]
    assert [evento["result"] for evento in eventos("seller.logo_set")][-2:] == [
        "succeeded",
        "succeeded",
    ]


def test_apagar_logo_e_idempotente_e_a_loja_fica_inteira() -> None:
    if ler(OWNER)["logo"] is None:
        gravar_logo(intent())
    changed, anterior = apagar_logo()
    assert changed is True and anterior is not None
    assert ler(OWNER)["logo"] is None
    changed, anterior = apagar_logo()
    assert (changed, anterior) == (False, None)
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select logo_object_key,logo_sha256,logo_width,logo_updated_at "
            "from seller_organization where id=%s",
            (LOJA,),
        ).fetchone()
    assert row == (None, None, None, None)


# --------------------------------------------------------------- promessa


def test_promessa_segue_a_regra_do_produto() -> None:
    versao = versao_do_produto()
    nova_versao, changed = promessa(EDITOR, "Feche mais apólices com IA", versao)
    assert changed is True and nova_versao == versao + 1
    # Versão velha em mãos: conflito, como qualquer campo do produto.
    with pytest.raises(psycopg.Error) as excinfo:
        promessa(OWNER, "Outra promessa", versao)
    assert sqlstate_de(excinfo.value) == "40001"
    mesma, changed = promessa(OWNER, "Feche mais apólices com IA", nova_versao)
    assert (mesma, changed) == (nova_versao, False)
    with pytest.raises(psycopg.Error) as excinfo:
        promessa(OWNER, " com espaço", nova_versao)
    assert sqlstate_de(excinfo.value) == "22023"
    registro = eventos("catalog.product_headline_updated")[-1]
    assert registro["target_id"] == PRODUTO
    assert registro["metadata"]["headline_before"] is None
    assert registro["metadata"]["headline_after"] == "Feche mais apólices com IA"
    # Sem capability de rascunho, ninguém escreve — nem o dono.
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        contexto(connection, OWNER, LOJA, UPDATE)
        with pytest.raises(psycopg.Error) as excinfo:
            connection.execute(
                "select * from app_private.update_product_headline(%s,%s,%s,%s,%s,%s,%s,%s)",
                (OWNER, LOJA, PRODUTO, None, nova_versao + 1, uuid4(), digest("body"), uuid4()),
            )
        assert sqlstate_de(excinfo.value) == "28000"


# ---------------------------------------------------------------- vitrine


def test_vitrine_so_devolve_marca_capa_e_promessa_quando_existem() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        marcar(connection, LOJA, com_marca=False)
        connection.commit()
    antes = vitrine("loja-de-teste")
    assert set(antes["seller"].keys()) == {"id", "slug", "name"}
    assert [item["slug"] for item in antes["products"]] == ["curso-publicado"]
    produto = antes["products"][0]
    assert "cover" not in produto and "headline" not in produto
    # O currículo vem do snapshot publicado: só títulos e contagens.
    assert produto["curriculum"] == {
        "lesson_count": 2,
        "material_count": 0,
        "lessons": ["Prospecção com IA", "Cotação em três prompts"],
    }
    assert "SECRETO" not in json.dumps(antes)

    with psycopg.connect(ADMIN_URL) as connection:
        marcar(connection, LOJA, com_marca=True)
        connection.execute(
            "update catalog_product set headline='Aprenda em duas aulas' "
            "where seller_organization_id=%s and id=%s",
            (LOJA, PUBLICADO),
        )
        connection.commit()
    try:
        depois = vitrine("loja-de-teste")
        assert depois["seller"]["tagline"] == "IA para corretores"
        assert depois["seller"]["about"] == "Sobre nós"
        assert depois["seller"]["accent_color"] == "#1a6b52"
        assert depois["seller"]["logo"]["media_type"] == "image/png"
        assert depois["seller"]["logo"]["width"] == 300
        assert depois["products"][0]["headline"] == "Aprenda em duas aulas"
        # O que a API troca por URL está aqui, e só aqui: chave e versão.
        assert set(depois["seller"]["logo"].keys()) == {
            "object_key",
            "storage_version_id",
            "media_type",
            "width",
            "height",
        }
    finally:
        with psycopg.connect(ADMIN_URL) as connection:
            marcar(connection, LOJA, com_marca=False)
            connection.execute(
                "update catalog_product set headline=null "
                "where seller_organization_id=%s and id=%s",
                (LOJA, PUBLICADO),
            )
            connection.commit()
    assert set(vitrine("loja-de-teste")["seller"].keys()) == {"id", "slug", "name"}


def test_resumo_do_curriculo_conta_materiais_limita_titulos_e_some_sem_aulas() -> None:
    assert ADMIN_URL
    aulas = [
        {"title": f"Aula {n}", "content": "SECRETO", "assets": [{"id": "a"}] * (n % 3)}
        for n in range(45)
    ]
    cheio = {"courses": [{"modules": [{"lessons": aulas}]}]}
    vazios = [
        {},
        {"courses": "nao-e-lista"},
        {"courses": []},
        {"courses": [{"modules": [{"lessons": [{"content": "sem título"}]}]}]},
    ]
    with psycopg.connect(ADMIN_URL) as connection:
        resumo = connection.execute(
            "select app_private.published_curriculum_summary(%s)", (Jsonb(cheio),)
        ).fetchone()
        assert resumo is not None
        assert resumo[0]["curriculum"]["lesson_count"] == 45
        assert resumo[0]["curriculum"]["material_count"] == sum(n % 3 for n in range(45))
        assert resumo[0]["curriculum"]["lessons"] == [f"Aula {n}" for n in range(40)]
        assert "SECRETO" not in json.dumps(resumo[0])
        for vazio in vazios:
            row = connection.execute(
                "select app_private.published_curriculum_summary(%s)", (Jsonb(vazio),)
            ).fetchone()
            assert row == ({},), vazio


def test_vitrine_nao_lista_o_rascunho() -> None:
    assert "produto-de-teste" not in json.dumps(vitrine("loja-de-teste"))


# ------------------------------------------------------------ área logada


def test_comprador_de_duas_lojas_nao_ve_marca_na_biblioteca() -> None:
    assert ADMIN_URL and RUNTIME_URL
    with psycopg.connect(ADMIN_URL) as connection:
        for loja in (LOJA, OUTRA_LOJA):
            marcar(connection, loja, com_marca=True)
        connection.commit()
    try:
        with psycopg.connect(RUNTIME_URL) as connection:
            contexto(connection, BUYER, None, "library.read")
            biblioteca = connection.execute(
                "select app_private.read_buyer_library(%s)", (BUYER,)
            ).fetchone()
            vitrine_b = connection.execute(
                "select app_private.read_buyer_seller_storefront(%s,%s)", (BUYER, ACESSO_B)
            ).fetchone()
        assert biblioteca is not None and vitrine_b is not None
        itens = biblioteca[0]
        assert {item["seller"]["id"] for item in itens} == {str(LOJA), str(OUTRA_LOJA)}
        for projecao in (itens, vitrine_b[0]):
            texto = json.dumps(projecao)
            for proibido in ("logo", "accent_color", "tagline", "#1a6b52", "IA para corretores"):
                assert proibido not in texto, proibido
    finally:
        with psycopg.connect(ADMIN_URL) as connection:
            for loja in (LOJA, OUTRA_LOJA):
                marcar(connection, loja, com_marca=False)
            connection.commit()
