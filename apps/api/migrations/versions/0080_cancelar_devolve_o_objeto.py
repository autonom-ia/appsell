"""Cancelar o envio devolve onde o arquivo estava (#642).

`POST …/uploads/{id}/cancel` respondia 401 sempre. A rota lia a intenção antes
de cancelar, para saber o bucket, a chave e o tamanho e abortar as partes já
enviadas. Essa leitura pedia o contexto de assinar
(`catalog.asset.upload.sign`) por continuação, e o autenticador só aceita
continuação entre pares permitidos, depois de uma sessão de domínio do mesmo
pedido. O cancelamento não é nenhum desses pares, então a leitura era recusada
antes de o banco ser consultado.

A saída é o próprio cancelamento devolver o que a rota precisa. A função já
lê a linha da intenção com `for update`, depois de autorizar o contexto de
cancelar; agora ela devolve também `storage` (driver, bucket, chave e tamanho
esperado). Assim a rota usa um contexto só, e o abort das partes deixa de
depender de uma segunda leitura.

O `storage` não é gravado no registro de idempotência: o retorno gravado
continua o mesmo de antes. Ele é acrescentado na saída, tanto no cancelamento
novo quanto na repetição, para que uma repetição depois de uma queda de rede
também consiga abortar as partes que a primeira tentativa não abortou.

A rota tira o `storage` antes de responder: bucket e chave não chegam ao
navegador.

Muda só o corpo de `app_private.cancel_asset_upload`, com a mesma assinatura,
o mesmo dono e os mesmos grants. O `search_path` ganha `pg_temp` no fim, no
padrão do #158. A descida devolve o corpo da 0008 sem alteração.

Revision ID: 0080_cancelar_devolve_o_objeto
Revises: 0070_codigo_da_compra
"""

from __future__ import annotations

from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0080_cancelar_devolve_o_objeto"
down_revision = "0070_codigo_da_compra"
branch_labels = None
depends_on = None

ASSINATURA = "app_private.cancel_asset_upload(uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,text,uuid)"
SEARCH_PATH = "set search_path=pg_catalog,app_private,public,pg_temp"
SEARCH_PATH_DA_0008 = "set search_path=pg_catalog,app_private,public"
_LOCK_TIMEOUT = "set local lock_timeout = '5s'"


def _carregar(revisao: str) -> ModuleType:
    caminho = Path(__file__).with_name(f"{revisao}.py")
    spec = importlib_util.spec_from_file_location(f"appsell_anterior_{revisao}_para_0080", caminho)
    if spec is None or spec.loader is None:  # pragma: no cover - caminho fixo no repo
        raise RuntimeError(f"a migration {revisao} nao pode ser lida")
    modulo = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


# A lista do readiness vem da anterior na cadeia (`_READY_ATE_<n>` dela).
READY_ANTES: str = getattr(_carregar(down_revision), f"_READY_ATE_{down_revision.split('_', 1)[0]}")
READY_DEPOIS = READY_ANTES[:-2] + f",'{revision}'))"
# Nome que as migrations seguintes leem por `getattr` (padrão da 0067 em diante).
_READY_ATE_0080 = READY_DEPOIS
_BASES_DO_READY: tuple[str, ...] = (
    "app_private.schema_ready_base_0016()",
    "app_private.schema_ready_base_0017()",
)
# Esta migration não acrescenta função ao readiness: a assinatura é a mesma.
# A próxima que acrescentar continua lendo `_ready_funcoes()` da 0070.
_ULTIMA_COM_FUNCAO_NO_READY = "0070_codigo_da_compra"

_REESCREVE = """
do $migration$
declare definicao text; reescrita text;
begin
  definicao := pg_get_functiondef('__ALVO__'::regprocedure);
  if (length(definicao)-length(replace(definicao,$de$__DE__$de$,'')))
       / length($de$__DE__$de$) <> 1 then
    raise exception '__ALVO__ nao continha a ancora exatamente uma vez' using errcode='55000';
  end if;
  reescrita := replace(definicao,$de$__DE__$de$,$para$__PARA__$para$);
  execute reescrita;
end $migration$
"""


def _reescreve(alvo: str, de: str, para: str) -> str:
    return _REESCREVE.replace("__ALVO__", alvo).replace("__DE__", de).replace("__PARA__", para)


# O corpo da 0008, com três mudanças: `pg_temp` no search_path, a variável
# `storage` e o `|| jsonb_build_object('storage',storage)` nos dois retornos.
CANCELAR_DEPOIS = """
        create or replace function app_private.cancel_asset_upload(
          p_actor uuid,p_seller uuid,p_product uuid,p_course uuid,p_module uuid,
          p_lesson uuid,p_upload uuid,p_idempotency uuid,p_body text,p_request uuid
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public,pg_temp as $$
        declare candidate app_private.asset_upload%rowtype; replay jsonb; result jsonb;
          storage jsonb;
        begin
          if p_idempotency is null or p_request is null or p_body is null
             or p_body!~'^[0-9a-f]{64}$' then
            raise exception 'invalid asset upload cancellation' using errcode='22023'; end if;
          select * into candidate from app_private.asset_upload
            where seller_organization_id=p_seller and catalog_product_id=p_product
              and course_id=p_course and module_id=p_module and lesson_id=p_lesson
              and id=p_upload for update;
          if not found then raise exception 'asset upload is unavailable' using errcode='28000'; end if;
          perform app_private.lock_curriculum_access(p_actor,p_seller,candidate.catalog_product_id,
            candidate.course_id,'catalog.asset.upload.cancel');
          storage:=jsonb_build_object('storage_driver',candidate.storage_driver,
            'bucket',candidate.quarantine_bucket,'object_key',candidate.quarantine_key,
            'byte_size',candidate.expected_byte_size);
          perform pg_advisory_xact_lock(hashtextextended(
            'appsell.asset/'||p_seller::text||'/upload.cancel/'||p_idempotency::text,0));
          replay:=app_private.asset_mutation_replay(p_seller,'upload.cancel',p_idempotency,
            p_actor,p_body,candidate.catalog_product_id,candidate.course_id,
            candidate.module_id,candidate.lesson_id,candidate.asset_id,p_upload);
          if replay is not null then
            return jsonb_set(replay,'{cancelled}','false'::jsonb,true)
              || jsonb_build_object('storage',storage);
          end if;
          if candidate.identity_user_id<>p_actor
             or candidate.status not in ('pending','uploaded','verifying') then
            raise exception 'asset upload cannot be cancelled' using errcode='P4091'; end if;
          update app_private.asset_upload set status='cancelled',updated_at=clock_timestamp()
            where seller_organization_id=p_seller and id=p_upload;
          update app_private.asset_verification_job set status='failed',
            lease_expires_at=null,updated_at=clock_timestamp()
            where seller_organization_id=p_seller and upload_id=p_upload
              and status in ('queued','leased');
          if candidate.observed_version_id is not null then
            perform app_private.enqueue_asset_storage_cleanup(p_seller,p_upload,'quarantine',
              candidate.storage_driver,candidate.quarantine_bucket,candidate.quarantine_key,
              candidate.observed_version_id);
          end if;
          result:=jsonb_build_object('upload_id',p_upload,'status','cancelled','cancelled',true);
          perform app_private.record_asset_mutation(p_seller,'upload.cancel',p_idempotency,
            p_actor,p_body,candidate.catalog_product_id,candidate.course_id,
            candidate.module_id,candidate.lesson_id,
            candidate.asset_id,p_upload,result);
          insert into audit_event(seller_organization_id,actor_identity_user_id,action,
            target_type,target_id,result,request_id,metadata)
          values(p_seller,p_actor,'catalog.asset_upload_cancelled','asset',candidate.asset_id,
            'succeeded',p_request,jsonb_build_object('upload_id',p_upload));
          return result || jsonb_build_object('storage',storage);
        end $$
        """

# O corpo exato da 0008, para a descida. `test_..._migration.py` confere que
# este texto existe, byte a byte, no arquivo da 0008.
CANCELAR_ANTES = """
        create function app_private.cancel_asset_upload(
          p_actor uuid,p_seller uuid,p_product uuid,p_course uuid,p_module uuid,
          p_lesson uuid,p_upload uuid,p_idempotency uuid,p_body text,p_request uuid
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare candidate app_private.asset_upload%rowtype; replay jsonb; result jsonb;
        begin
          if p_idempotency is null or p_request is null or p_body is null
             or p_body!~'^[0-9a-f]{64}$' then
            raise exception 'invalid asset upload cancellation' using errcode='22023'; end if;
          select * into candidate from app_private.asset_upload
            where seller_organization_id=p_seller and catalog_product_id=p_product
              and course_id=p_course and module_id=p_module and lesson_id=p_lesson
              and id=p_upload for update;
          if not found then raise exception 'asset upload is unavailable' using errcode='28000'; end if;
          perform app_private.lock_curriculum_access(p_actor,p_seller,candidate.catalog_product_id,
            candidate.course_id,'catalog.asset.upload.cancel');
          perform pg_advisory_xact_lock(hashtextextended(
            'appsell.asset/'||p_seller::text||'/upload.cancel/'||p_idempotency::text,0));
          replay:=app_private.asset_mutation_replay(p_seller,'upload.cancel',p_idempotency,
            p_actor,p_body,candidate.catalog_product_id,candidate.course_id,
            candidate.module_id,candidate.lesson_id,candidate.asset_id,p_upload);
          if replay is not null then return jsonb_set(replay,'{cancelled}','false'::jsonb,true); end if;
          if candidate.identity_user_id<>p_actor
             or candidate.status not in ('pending','uploaded','verifying') then
            raise exception 'asset upload cannot be cancelled' using errcode='P4091'; end if;
          update app_private.asset_upload set status='cancelled',updated_at=clock_timestamp()
            where seller_organization_id=p_seller and id=p_upload;
          update app_private.asset_verification_job set status='failed',
            lease_expires_at=null,updated_at=clock_timestamp()
            where seller_organization_id=p_seller and upload_id=p_upload
              and status in ('queued','leased');
          if candidate.observed_version_id is not null then
            perform app_private.enqueue_asset_storage_cleanup(p_seller,p_upload,'quarantine',
              candidate.storage_driver,candidate.quarantine_bucket,candidate.quarantine_key,
              candidate.observed_version_id);
          end if;
          result:=jsonb_build_object('upload_id',p_upload,'status','cancelled','cancelled',true);
          perform app_private.record_asset_mutation(p_seller,'upload.cancel',p_idempotency,
            p_actor,p_body,candidate.catalog_product_id,candidate.course_id,
            candidate.module_id,candidate.lesson_id,
            candidate.asset_id,p_upload,result);
          insert into audit_event(seller_organization_id,actor_identity_user_id,action,
            target_type,target_id,result,request_id,metadata)
          values(p_seller,p_actor,'catalog.asset_upload_cancelled','asset',candidate.asset_id,
            'succeeded',p_request,jsonb_build_object('upload_id',p_upload));
          return result;
        end $$
        """

GRANTS = (
    f"revoke all on function {ASSINATURA} from public",
    f"grant execute on function {ASSINATURA} to appsell_runtime",
)


def upgrade() -> None:
    op.execute(_LOCK_TIMEOUT)
    op.execute(CANCELAR_DEPOIS)
    for sql in GRANTS:
        op.execute(sql)
    for alvo in _BASES_DO_READY:
        op.execute(_reescreve(alvo, READY_ANTES, READY_DEPOIS))


def downgrade() -> None:
    op.execute(_LOCK_TIMEOUT)
    for alvo in _BASES_DO_READY:
        op.execute(_reescreve(alvo, READY_DEPOIS, READY_ANTES))
    op.execute(CANCELAR_ANTES.replace("create function", "create or replace function", 1))
    for sql in GRANTS:
        op.execute(sql)
