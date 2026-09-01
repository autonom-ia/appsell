"""O `confirm` aprende que envio em partes não produz hash do arquivo inteiro.

Uma aula de 45 min em 1080p passa de 800 MB. O envio de hoje é um `PUT` único,
com URL assinada de 300 s e o cliente desistindo aos 290 s: numa linha de
10 Mbit/s esse arquivo leva 640 s, a URL vence antes, e o vendedor perde o envio
inteiro sem ter de onde retomar. O envio em partes resolve isso, e traz uma
consequência no caminho.

## O que muda, e por quê

O protocolo do storage **não calcula hash de arquivo inteiro** quando o envio é
fatiado, exceto com algoritmos CRC. SHA-256 não é um deles: o que o storage
guarda é um hash composto das partes, com o número de partes no fim. Então a
comparação que o `confirm` fazia — hash do objeto contra `expected_sha256` — não
tem como continuar existindo nessa forma para arquivo grande.

Ela passa a ser explicitamente **adiada para a verificação**, que já relê todos
os bytes e compara com o mesmo `expected_sha256` antes de promover qualquer
coisa. Nada é promovido sem o hash bater. O que muda é o momento em que o
desacordo aparece, e o audit registra qual dos dois caminhos foi usado.

Na prática o desacordo aparece **mais cedo**, não mais tarde: cada parte é
assinada com o hash dela preso na assinatura, e o storage recusa a parte
alterada no ato em que ela sobe, em vez de no fim do arquivo.

O que **não** muda: o tamanho continua conferido no `confirm`, exato, contra o
declarado na intenção.

## Por que o teto da parte mora aqui também

Quem decide se um envio foi fatiado é o banco, a partir do tamanho declarado, e
não um sinalizador de quem chama: um sinalizador poderia pedir para pular a
conferência de um arquivo pequeno. Isso obriga o número da parte a existir no
banco, como já acontece com todo limite de mídia (Decisão 010).
`test_envio_em_partes.py` compara os dois lados.

Revision ID: 0045_envio_em_partes
Revises: 0044_pdf_de_trezentas_paginas
"""

from __future__ import annotations

from alembic import op

revision = "0045_envio_em_partes"
down_revision = "0044_pdf_de_trezentas_paginas"
branch_labels = None
depends_on = None

#: O tamanho de cada parte, espelhado de `storage_s3.MULTIPART_PART_BYTES`.
_PART_BYTES = 16 * 1024 * 1024

_READY_ATE_0044 = "'0043_material_role_by_media','0044_pdf_de_trezentas_paginas'))"
_READY_ATE_0045 = (
    "'0043_material_role_by_media','0044_pdf_de_trezentas_paginas','0045_envio_em_partes'))"
)

#: As duas cópias da função que carregam a lista de versões conhecidas.
_BASES_DO_READY: tuple[str, ...] = (
    "app_private.schema_ready_base_0016()",
    "app_private.schema_ready_base_0017()",
)

_ESTENDE_O_READY = """
do $migration$
declare
  definicao text;
  reescrita text;
begin
  definicao := pg_get_functiondef('ALVO'::regprocedure);
  reescrita := replace(definicao, $de$DE$de$, $para$PARA$para$);
  if reescrita = definicao then
    raise exception 'ALVO não continha a lista esperada de versões' using errcode='55000';
  end if;
  execute reescrita;
end $migration$;
"""


def _troca_no_readiness(de: str, para: str) -> None:
    """Ensina `schema_ready()` a reconhecer esta migration.

    Sem isto o banco fica "não pronto" depois do upgrade: a versão avança e a
    lista não. Como o `/health/ready` alimenta o `HEALTHCHECK` do contêiner,
    isso não devolve um número errado — derruba a API em laço de reinício.
    """
    for alvo in _BASES_DO_READY:
        op.execute(_ESTENDE_O_READY.replace("ALVO", alvo).replace("DE", de).replace("PARA", para))


_TAMANHO_DA_PARTE = f"""
create or replace function app_private.asset_multipart_part_bytes()
returns bigint language sql immutable
set search_path=pg_catalog as $$ select {_PART_BYTES}::bigint $$;
-- O mesmo bloco que toda função desta série carrega. Sem ele vale o padrão do
-- PostgreSQL, que concede execução a `public`: esta seria a única função do
-- schema chamável por qualquer role do banco.
alter function app_private.asset_multipart_part_bytes() owner to appsell_owner;
revoke all on function app_private.asset_multipart_part_bytes() from public;
grant execute on function app_private.asset_multipart_part_bytes() to appsell_runtime;
"""  # noqa: S608 - o único valor interpolado é a constante deste módulo


def _confirm(*, aceita_hash_nulo: bool) -> str:
    """A função de confirmação, com ou sem a conferência adiada.

    As duas versões são geradas do mesmo texto de propósito: escrever a antiga à
    mão para o `downgrade` foi como, em outras bases, uma reversão deixou para
    trás uma diferença que ninguém pediu.
    """
    validacao_do_hash = (
        "(p_observed_checksum is not null and p_observed_checksum!~'^[0-9a-f]{64}$')"
        if aceita_hash_nulo
        else "p_observed_checksum is null or p_observed_checksum!~'^[0-9a-f]{64}$'"
    )
    exige_hash = (
        """
          fatiado := candidate.expected_byte_size > app_private.asset_multipart_part_bytes();
          -- Envio fatiado não produz hash do arquivo inteiro, e por isso a
          -- conferência de conteúdo dele é a da verificação, que relê todos os
          -- bytes contra este mesmo `expected_sha256`. Para envio único o hash
          -- continua obrigatório aqui.
          if p_observed_checksum is null and not fatiado then
            raise exception 'invalid asset upload confirmation' using errcode='22023';
          end if;
        """
        if aceita_hash_nulo
        else "          fatiado := false;\n"
    )
    # O `content_check` só existe a partir desta migration. Deixá-lo no corpo
    # antigo faria o `downgrade` devolver uma função que ainda grava um campo
    # que nunca existiu antes — e o downgrade promete devolver o que era.
    registro_do_audit = (
        "jsonb_build_object('upload_id',p_upload,"
        "'content_check',case when fatiado then 'deferred_to_verification'"
        " else 'head_checksum' end)"
        if aceita_hash_nulo
        else "jsonb_build_object('upload_id',p_upload)"
    )
    compara_hash = (
        "(p_observed_checksum is not null and p_observed_checksum<>candidate.expected_sha256)"
        if aceita_hash_nulo
        else "p_observed_checksum<>candidate.expected_sha256"
    )
    return f"""
        create or replace function app_private.confirm_asset_upload(
          p_actor uuid,p_seller uuid,p_product uuid,p_course uuid,p_module uuid,
          p_lesson uuid,p_upload uuid,p_observed_version_id text,
          p_observed_size bigint,p_observed_checksum text,p_observed_etag text,
          p_idempotency uuid,p_body text,p_request uuid
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare candidate app_private.asset_upload%rowtype; replay jsonb; result jsonb;
                fatiado boolean;
        begin
          if p_observed_version_id is null or octet_length(p_observed_version_id)>512
             or p_observed_etag is null or octet_length(p_observed_etag)>256
             or {validacao_do_hash}
             or p_observed_size not between 1 and 104857600 or p_idempotency is null
             or p_request is null or p_body is null or p_body!~'^[0-9a-f]{{64}}$' then
            raise exception 'invalid asset upload confirmation' using errcode='22023';
          end if;
          select * into candidate from app_private.asset_upload
            where seller_organization_id=p_seller and catalog_product_id=p_product
              and course_id=p_course and module_id=p_module and lesson_id=p_lesson
              and id=p_upload for update;
          if not found then raise exception 'asset upload is unavailable' using errcode='28000'; end if;
{exige_hash}
          perform app_private.lock_curriculum_access(p_actor,p_seller,candidate.catalog_product_id,
            candidate.course_id,'catalog.asset.upload.confirm');
          perform pg_advisory_xact_lock(hashtextextended(
            'appsell.asset/'||p_seller::text||'/upload.confirm/'||p_idempotency::text,0));
          replay:=app_private.asset_mutation_replay(p_seller,'upload.confirm',p_idempotency,
            p_actor,p_body,candidate.catalog_product_id,candidate.course_id,
            candidate.module_id,candidate.lesson_id,candidate.asset_id,p_upload);
          if replay is not null then return jsonb_set(replay,'{{confirmed}}','false'::jsonb,true); end if;
          if candidate.identity_user_id<>p_actor or candidate.status<>'pending'
             or candidate.expires_at<=clock_timestamp() then
            raise exception 'asset upload is unavailable' using errcode='28000';
          end if;
          if p_observed_size<>candidate.expected_byte_size
             or {compara_hash} then
            update app_private.asset_upload set status='rejected',failure_code='head_mismatch',
              updated_at=clock_timestamp() where seller_organization_id=p_seller and id=p_upload;
            perform app_private.enqueue_asset_storage_cleanup(p_seller,p_upload,'quarantine',
              candidate.storage_driver,candidate.quarantine_bucket,candidate.quarantine_key,
              p_observed_version_id);
            result:=jsonb_build_object('upload_id',p_upload,'status','rejected',
              'safe_reason','upload_mismatch','confirmed',false);
            perform app_private.record_asset_mutation(p_seller,'upload.confirm',p_idempotency,
              p_actor,p_body,candidate.catalog_product_id,candidate.course_id,
              candidate.module_id,candidate.lesson_id,
              candidate.asset_id,p_upload,result);
            insert into audit_event(seller_organization_id,actor_identity_user_id,action,
              target_type,target_id,result,request_id,metadata)
            values(p_seller,p_actor,'catalog.asset_upload_rejected','asset',candidate.asset_id,
              'failed',p_request,jsonb_build_object('upload_id',p_upload,
                'safe_reason','upload_mismatch'));
            return result;
          end if;
          update app_private.asset_upload set status='uploaded',
            observed_version_id=p_observed_version_id,observed_etag=p_observed_etag,
            updated_at=clock_timestamp() where seller_organization_id=p_seller and id=p_upload;
          insert into app_private.asset_verification_job(seller_organization_id,upload_id,status)
            values(p_seller,p_upload,'queued');
          result:=jsonb_build_object('upload_id',p_upload,'status','uploaded','confirmed',true);
          perform app_private.record_asset_mutation(p_seller,'upload.confirm',p_idempotency,
            p_actor,p_body,candidate.catalog_product_id,candidate.course_id,
            candidate.module_id,candidate.lesson_id,
            candidate.asset_id,p_upload,result);
          insert into audit_event(seller_organization_id,actor_identity_user_id,action,
            target_type,target_id,result,request_id,metadata)
          values(p_seller,p_actor,'catalog.asset_upload_confirmed','asset',candidate.asset_id,
            'succeeded',p_request,{registro_do_audit});
          return result;
        end $$
        """  # noqa: S608 - o corpo é literal deste módulo; só os dois trechos variam


def upgrade() -> None:
    op.execute(_TAMANHO_DA_PARTE)
    op.execute(_confirm(aceita_hash_nulo=True))
    _troca_no_readiness(_READY_ATE_0044, _READY_ATE_0045)


def downgrade() -> None:
    # Voltar só é seguro se nenhum envio grande estiver no meio do caminho: a
    # função antiga exige o hash do arquivo inteiro, que esses envios não têm.
    # Confirmá-los depois da reversão viraria `head_mismatch` num arquivo bom.
    op.execute(
        f"""
        do $$
        declare quantos bigint;
        begin
          select count(*) into quantos from app_private.asset_upload
            where status='pending' and expected_byte_size > {_PART_BYTES};
          if quantos > 0 then
            raise exception
              'não dá para voltar: % envios em partes ainda pendentes', quantos
              using errcode='55000';
          end if;
        end $$;
        """  # noqa: S608 - o número é constante deste módulo
    )
    op.execute(_confirm(aceita_hash_nulo=False))
    op.execute("drop function if exists app_private.asset_multipart_part_bytes()")
    _troca_no_readiness(_READY_ATE_0045, _READY_ATE_0044)
