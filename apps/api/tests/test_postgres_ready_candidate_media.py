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
    # O grafo (produto, curso, módulo, aula) precisa de um asset qualquer; o
    # formato dele não tem relação com o do envio que estamos testando.
    asset = add_asset(connection, graph, media_type=PDF_MEDIA_TYPE, position=0)
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
            asset.asset,
            uuid4(),
            media_type,
            digest(f"source-{upload}"),
            f"tenants/{graph.seller}/quarantine/{upload}.{extension}",
            f"tenants/{graph.seller}/assets/{asset.asset}/v2/original.{extension}",
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


@pytest.fixture
def limpeza() -> Iterator[list[tuple[UUID, UUID]]]:
    """Nada deste arquivo pode sobrar no banco compartilhado.

    O registro do candidato enfileira uma limpeza de storage, e
    `claim_asset_storage_cleanup_jobs` varre a fila inteira. Um envio esquecido
    aqui reaparece como uma linha a mais na contagem de outro teste — que passa
    sozinho e falha na suite, o pior tipo de falso vermelho.
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
