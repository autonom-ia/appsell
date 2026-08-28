"""O documento chega ao fim do caminho de verificação.

## O que este arquivo prova

Um DOCX, um XLSX, um PPTX, um TXT, um MD, um CSV ou um JSON sobe, passa no
antivírus, é verificado — e morria na última porta:

    or p_ready_media not in ('application/pdf','video/mp4')
      then raise exception 'invalid ready asset candidate' using errcode='22023'

`register_asset_ready_candidate_v1_legacy` era a única função do caminho que
restringia formato. O intent aceitava, o confirm aceitava, o `finalize` aceitava,
a constraint de `asset_blob` aceitava, a de `asset_storage_cleanup_job` aceitava
— e o registro do candidato recusava. Documento **nunca** pôde ser registrado,
desde que os sete formatos entraram no catálogo.

E o worker engolia o erro sem log: `except DBAPIError: ...` tentava de novo,
compensava e retornava. O vendedor via "a verificação está demorando mais que o
esperado", que promete que vai resolver sozinho.

Por isso o teste chama a função de verdade, formato por formato, em vez de ler o
texto dela: ler o texto prova que o literal saiu, chamar prova que o arquivo
passa.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from test_postgres_pdf_access_policy import (
    OWNER,
    add_asset,
    apply_context,
    create_graph,
    digest,
    seed_identities,
)

from appsell_api.asset_media import PDF_MEDIA_TYPE, SUPPORTED_MEDIA, VIDEO_MEDIA_TYPE

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.environ.get("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY_HEX = os.environ.get("APPSELL_TEST_CONTEXT_KEY_HEX")

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY_HEX)),
    reason="isolated PostgreSQL environment is required",
)

WORKER = UUID("82000000-0000-4000-8000-000000000009")

# O PDF tem porta própria (exige manifesto de raster) e o MP4 já passava. O que
# este arquivo cobre é o que nunca passou.
DOCUMENT_MEDIA = tuple(
    media for media in SUPPORTED_MEDIA if media not in (PDF_MEDIA_TYPE, VIDEO_MEDIA_TYPE)
)


@pytest.fixture(scope="module", autouse=True)
def identities() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        seed_identities(connection)
        connection.execute(
            """
            insert into identity_user(
              id,identity_issuer,identity_sub,email_normalized,email_verified
            ) values(%s,'https://identity.ready-media.test','ready-media-worker',
              'ready-media-worker@example.test',true)
            on conflict(id) do nothing
            """,
            (WORKER,),
        )
        connection.execute(
            """
            insert into app_private.asset_worker_identity(identity_user_id,status,label)
            values(%s,'active','ready-media-test-worker')
            on conflict(identity_user_id) do update set status='active'
            """,
            (WORKER,),
        )
        connection.commit()


def _seed_upload(connection: psycopg.Connection[Any], media_type: str) -> tuple[UUID, UUID, str]:
    """Deixa um envio em `verifying`, com lease vivo, pronto para o candidato."""
    graph = create_graph(connection)
    # O grafo (produto, curso, módulo, aula) precisa de um asset qualquer para
    # existir; o formato dele não tem relação com o do envio testado aqui.
    add_asset(connection, graph, media_type=PDF_MEDIA_TYPE, position=0)
    # O envio aponta para um asset **novo**, sem rascunho. Reaproveitar o asset
    # do grafo mediria o caminho de substituição, e ali o `on conflict do update`
    # preserva o papel de propósito: reenviar um arquivo não pode desfazer a
    # classificação que o vendedor já deu.
    asset_id = uuid4()
    connection.execute(
        "insert into asset_record(seller_organization_id,id,catalog_product_id,title) "
        "values(%s,%s,%s,%s)",
        (graph.seller, asset_id, graph.product, f"Material {media_type}"),
    )
    extension = SUPPORTED_MEDIA[media_type].extension
    upload = uuid4()
    connection.execute(
        f"""
        insert into app_private.asset_upload(
          seller_organization_id,id,identity_user_id,catalog_product_id,course_id,
          module_id,lesson_id,asset_id,blob_id,reserved_version,title,file_name,
          expected_media_type,expected_byte_size,expected_sha256,storage_driver,
          quarantine_bucket,ready_bucket,quarantine_key,ready_key,observed_version_id,
          observed_etag,status,expires_at
        ) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,2,'Material','material.{extension}',
          %s,8192,%s,'s3','appsell-quarantine','appsell-ready',
          %s,%s,'source-version','source-etag','verifying',
          clock_timestamp()+interval '1 hour')
        """,
        (
            graph.seller,
            upload,
            OWNER,
            graph.product,
            graph.course,
            graph.module,
            graph.lesson,
            asset_id,
            uuid4(),
            media_type,
            digest(f"source-{upload}"),
            f"tenants/{graph.seller}/quarantine/{upload}.{extension}",
            f"tenants/{graph.seller}/assets/{asset_id}/v2/original.{extension}",
        ),
    )
    connection.execute(
        """
        insert into app_private.asset_verification_job(
          seller_organization_id,upload_id,status,attempts,worker_id,lease_expires_at
        ) values(%s,%s,'leased',1,'media-worker',clock_timestamp()+interval '5 minutes')
        """,
        (graph.seller, upload),
    )
    connection.commit()
    return graph.seller, upload, digest(f"ready-{upload}")


def _seed_replacement(
    connection: psycopg.Connection[Any], seller: UUID, asset_id: UUID, media_type: str
) -> tuple[UUID, str]:
    """Um envio novo para um asset que já existe: o caminho de substituição."""
    extension = SUPPORTED_MEDIA[media_type].extension
    upload = uuid4()
    row = connection.execute(
        "select catalog_product_id,course_id,module_id,lesson_id,reserved_version "
        "from app_private.asset_upload where asset_id=%s and seller_organization_id=%s "
        "order by created_at desc limit 1",
        (asset_id, seller),
    ).fetchone()
    assert row is not None
    product, course, module, lesson, version = row
    connection.execute(
        f"""
        insert into app_private.asset_upload(
          seller_organization_id,id,identity_user_id,catalog_product_id,course_id,
          module_id,lesson_id,asset_id,blob_id,reserved_version,title,file_name,
          expected_media_type,expected_byte_size,expected_sha256,storage_driver,
          quarantine_bucket,ready_bucket,quarantine_key,ready_key,observed_version_id,
          observed_etag,status,expires_at
        ) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'Material','material.{extension}',
          %s,8192,%s,'s3','appsell-quarantine','appsell-ready',
          %s,%s,'source-version','source-etag','verifying',
          clock_timestamp()+interval '1 hour')
        """,
        (
            seller,
            upload,
            OWNER,
            product,
            course,
            module,
            lesson,
            asset_id,
            uuid4(),
            int(version) + 1,
            media_type,
            digest(f"source-{upload}"),
            f"tenants/{seller}/quarantine/{upload}.{extension}",
            f"tenants/{seller}/assets/{asset_id}/v{int(version) + 1}/original.{extension}",
        ),
    )
    connection.execute(
        "insert into app_private.asset_verification_job("
        "seller_organization_id,upload_id,status,attempts,worker_id,lease_expires_at) "
        "values(%s,%s,'leased',1,'media-worker',clock_timestamp()+interval '5 minutes')",
        (seller, upload),
    )
    connection.commit()
    return upload, digest(f"ready-{upload}")


@pytest.fixture
def limpeza() -> Iterator[list[tuple[UUID, UUID]]]:
    """Esvazia a fila de limpeza de storage que estes testes enchem.

    O registro do candidato enfileira uma limpeza, e
    `claim_asset_storage_cleanup_jobs` varre a fila **inteira**, sem filtro de
    tenant. Um envio esquecido aqui reaparece como uma linha a mais na contagem
    de outro teste — que passa sozinho e falha na suíte, o pior tipo de falso
    vermelho. Aconteceu na primeira versão deste arquivo.

    O grafo (vendedor, produto, curso, aula) fica: ele é tenant-isolado e nenhuma
    varredura global o alcança, e todos os testes de Postgres desta base o
    deixam para trás do mesmo jeito.
    """
    criados: list[tuple[UUID, UUID]] = []
    yield criados
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        for seller, upload in criados:
            connection.execute(
                "delete from app_private.asset_storage_cleanup_job "
                "where seller_organization_id=%s and upload_id=%s",
                (seller, upload),
            )
            connection.execute(
                "delete from app_private.asset_verification_job "
                "where seller_organization_id=%s and upload_id=%s",
                (seller, upload),
            )
            connection.execute(
                "delete from app_private.asset_upload where seller_organization_id=%s and id=%s",
                (seller, upload),
            )
        connection.commit()


@pytest.mark.parametrize("media_type", DOCUMENT_MEDIA)
def test_o_documento_e_registrado_como_candidato_pronto(
    media_type: str, limpeza: list[tuple[UUID, UUID]]
) -> None:
    """A porta que recusava DOCX, XLSX, PPTX, TXT, MD, CSV e JSON."""
    assert ADMIN_URL and RUNTIME_URL
    with psycopg.connect(ADMIN_URL) as connection:
        seller, upload, ready_sha = _seed_upload(connection, media_type)
    limpeza.append((seller, upload))

    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, WORKER, None, "catalog.asset.verify")
        row = connection.execute(
            """
            select app_private.register_asset_ready_candidate(
              %s,%s,%s,'media-worker','ready-version','ready-etag',8192,%s,%s,%s,'clean')
            """,
            (WORKER, seller, upload, ready_sha, media_type, media_type),
        ).fetchone()
        connection.commit()

    assert row is not None
    registered: dict[str, Any] = row[0]
    assert registered["registered"] is True, f"{media_type} foi recusado no registro do candidato"
    assert registered["reconciling"] is False, (
        f"{media_type} entrou pelo caminho de reconciliação — o lease estava vivo"
    )


def test_um_formato_fora_do_catalogo_continua_recusado(
    limpeza: list[tuple[UUID, UUID]],
) -> None:
    """Derivar do catálogo não pode virar 'aceita qualquer coisa'."""
    assert ADMIN_URL and RUNTIME_URL
    with psycopg.connect(ADMIN_URL) as connection:
        seller, upload, ready_sha = _seed_upload(connection, "text/markdown")
    limpeza.append((seller, upload))

    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, WORKER, None, "catalog.asset.verify")
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            connection.execute(
                """
                select app_private.register_asset_ready_candidate(
                  %s,%s,%s,'media-worker','ready-version','ready-etag',8192,%s,
                  'application/x-msdownload','application/x-msdownload','clean')
                """,
                (WORKER, seller, upload, ready_sha),
            )
        connection.rollback()


@pytest.mark.parametrize("media_type", DOCUMENT_MEDIA)
def test_o_documento_nasce_como_material_de_apoio_que_o_aluno_baixa(
    media_type: str, limpeza: list[tuple[UUID, UUID]]
) -> None:
    """O papel do material vem do formato, não de uma pergunta na tela.

    `lesson_asset_draft.content_role` tem `default 'course_content'`, e quem
    insere a linha é o worker, no `finalize`. Todo material nascia como conteúdo
    protegido sem download — inclusive um `.md` de 10 KiB, que só existe para o
    aluno baixar. A tela compensava com um `<select>` em cada card, perguntando
    ao vendedor uma coisa que o catálogo já sabia.

    Este teste **grava**: registra o candidato, finaliza e lê a linha. Ler o
    texto da função provaria só que o SQL mudou — e foi exatamente uma checagem
    de texto que, mais cedo neste mesmo dia, passou verde por cima de uma
    divergência viva.
    """
    assert ADMIN_URL and RUNTIME_URL
    with psycopg.connect(ADMIN_URL) as connection:
        seller, upload, ready_sha = _seed_upload(connection, media_type)
    limpeza.append((seller, upload))

    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, WORKER, None, "catalog.asset.verify")
        connection.execute(
            """
            select app_private.register_asset_ready_candidate(
              %s,%s,%s,'media-worker','ready-version','ready-etag',8192,%s,%s,%s,'clean')
            """,
            (WORKER, seller, upload, ready_sha, media_type, media_type),
        )
        connection.execute(
            """
            select app_private.finalize_asset_verification(
              %s,%s,%s,'media-worker',8192,%s,8192,%s,%s,%s,'clean',
              'ready-version','ready-etag','ready',%s,%s,%s)
            """,
            (
                WORKER,
                seller,
                upload,
                digest(f"source-{upload}"),
                ready_sha,
                media_type,
                media_type,
                uuid4(),
                digest(f"body-{upload}"),
                uuid4(),
            ),
        )
        connection.commit()

    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            """
            select draft.content_role, draft.download_allowed
            from lesson_asset_draft draft
            join app_private.asset_upload upload
              on upload.seller_organization_id=draft.seller_organization_id
             and upload.asset_id=draft.asset_id
            where upload.id=%s
            """,
            (upload,),
        ).fetchone()

    assert row is not None, f"{media_type}: o material nao chegou ao rascunho da aula"
    assert row[0] == "support_material", (
        f"{media_type} nasceu como {row[0]} — o vendedor teria de corrigir na mao"
    )
    assert row[1] is True, f"{media_type} nasceu sem permissao de download e nao serve para nada"


def test_o_player_declara_o_que_sabe_apresentar() -> None:
    """A regra que decide o papel, exercitada formato a formato.

    PDF e MP4 o player desenha — leitor paginado e player de vídeo. O resto o
    aluno baixa. É a mesma regra que `learning_asset_media_contract_check`
    declara desde a `0017`; a `0043` só lhe deu nome e um lugar só.
    """
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        for media_type in SUPPORTED_MEDIA:
            row = connection.execute(
                "select app_private.player_renders_media(%s)", (media_type,)
            ).fetchone()
            assert row is not None
            esperado = media_type in (PDF_MEDIA_TYPE, VIDEO_MEDIA_TYPE)
            assert row[0] is esperado, f"{media_type}: o player diz {row[0]}, esperado {esperado}"
        desconhecido = connection.execute(
            "select app_private.player_renders_media('application/x-msdownload')"
        ).fetchone()
    assert desconhecido is not None and desconhecido[0] is False


def test_substituir_por_video_desliga_o_download_que_o_documento_tinha(
    limpeza: list[tuple[UUID, UUID]],
) -> None:
    """Preservar a escolha do vendedor não pode gravar estado impossível.

    O `on conflict do update` preserva a classificação — reenviar um arquivo não
    desfaz o que o vendedor decidiu. Mas preservar às cegas é outra coisa:
    trocar um `.md` baixável por um MP4 manteria `download_allowed=true` num
    vídeo, e a constraint da tabela **não** pega isso. Ela só exige que download
    implique `support_material`; a mídia ela não olha.

    `set_lesson_asset_policy` recusaria essa combinação vinda da tela. Chegando
    pelo `finalize`, entraria calada.
    """
    assert ADMIN_URL and RUNTIME_URL
    with psycopg.connect(ADMIN_URL) as connection:
        seller, upload, ready_sha = _seed_upload(connection, "text/markdown")
    limpeza.append((seller, upload))

    def verificar(media_type: str, upload_id: UUID, sha: str) -> None:
        with psycopg.connect(RUNTIME_URL) as connection:
            apply_context(connection, WORKER, None, "catalog.asset.verify")
            connection.execute(
                "select app_private.register_asset_ready_candidate("
                "%s,%s,%s,'media-worker','ready-version','ready-etag',8192,%s,%s,%s,'clean')",
                (WORKER, seller, upload_id, sha, media_type, media_type),
            )
            connection.execute(
                """
                select app_private.finalize_asset_verification(
                  %s,%s,%s,'media-worker',8192,%s,8192,%s,%s,%s,'clean',
                  'ready-version','ready-etag','ready',%s,%s,%s)
                """,
                (
                    WORKER,
                    seller,
                    upload_id,
                    digest(f"source-{upload_id}"),
                    sha,
                    media_type,
                    media_type,
                    uuid4(),
                    digest(f"body-{upload_id}"),
                    uuid4(),
                ),
            )
            connection.commit()

    verificar("text/markdown", upload, ready_sha)

    with psycopg.connect(ADMIN_URL) as connection:
        antes = connection.execute(
            "select draft.content_role, draft.download_allowed from lesson_asset_draft draft "
            "join app_private.asset_upload upload on upload.asset_id=draft.asset_id "
            "and upload.seller_organization_id=draft.seller_organization_id where upload.id=%s",
            (upload,),
        ).fetchone()
        assert antes == ("support_material", True)

        # A substituição reusa o mesmo asset, com um envio novo.
        asset_id = connection.execute(
            "select asset_id from app_private.asset_upload where id=%s", (upload,)
        ).fetchone()
        assert asset_id is not None
        substituicao, sha = _seed_replacement(connection, seller, asset_id[0], "video/mp4")
    limpeza.append((seller, substituicao))

    verificar("video/mp4", substituicao, sha)

    with psycopg.connect(ADMIN_URL) as connection:
        depois = connection.execute(
            "select draft.content_role, draft.download_allowed from lesson_asset_draft draft "
            "join app_private.asset_upload upload on upload.asset_id=draft.asset_id "
            "and upload.seller_organization_id=draft.seller_organization_id where upload.id=%s",
            (substituicao,),
        ).fetchone()

    assert depois is not None
    assert depois[1] is False, "o video ficou com download ligado, herdado do documento"
    assert depois[0] == "support_material", (
        "o papel escolhido pelo vendedor foi desfeito pela substituicao"
    )
