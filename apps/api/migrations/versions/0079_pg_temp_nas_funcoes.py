"""`pg_temp` no fim do `search_path` de toda função `security definer` (#598).

As migrations até a 0061 fixaram o `search_path` das funções `security
definer` sem `pg_temp`. Sem ele na lista, o Postgres procura tabelas e tipos no
schema temporário da sessão **antes** dos schemas listados, e quem pode criar
tabela temporária poderia sombrear uma tabela usada por uma função que roda com
os privilégios do dono. O padrão do #158 (seguido da 0062 em diante e pela 0076)
fixa `pg_temp` no fim.

Esta migration alinha as 129 funções que ainda seguiam a convenção antiga,
medidas num banco descartável com a cadeia inteira até a 0072. Cada uma ganha
`, pg_temp` no fim do valor que já tinha, por `alter function ... set
search_path`. Corpo, dono, grant e comportamento não mudam.

Antes de alterar, cada função tem o valor atual conferido contra o esperado. Se
algum divergir, a migration aborta dizendo a assinatura, o valor esperado e o
encontrado: um banco que não está no estado medido não é alterado às cegas. O
`downgrade` confere o valor de depois e devolve o de antes, um a um.

A lista mora aqui, e não só no catálogo, porque o `downgrade` precisa dela:
depois do upgrade, estas funções ficam iguais às que já tinham `pg_temp`. Os
valores de antes já são públicos (estão no `create function` de cada migration
que criou a função). O teste `test_postgres_search_path_pg_temp.py` reprova
qualquer função `security definer` futura sem `pg_temp` no fim.

Revision ID: 0079_pg_temp_nas_funcoes
Revises: 0072_relatorio_de_vendas
"""

from __future__ import annotations

from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0079_pg_temp_nas_funcoes"
down_revision = "0072_relatorio_de_vendas"
branch_labels = None
depends_on = None

_COMPLETO = "pg_catalog, app_private, public"
_PRIVADO = "pg_catalog, app_private"
_PUBLICO = "pg_catalog, public"
_CATALOGO = "pg_catalog"
PG_TEMP = ", pg_temp"

# Assinatura (como o Postgres a escreve em `regprocedure`) e valor de antes. O
# valor de depois é o de antes com `, pg_temp` no fim.
FUNCOES: tuple[tuple[str, str], ...] = (
    (
        "app_private.accept_checkout_webhook(uuid,uuid,text,text,bigint,text,smallint,text,integer,text,text,smallint,text,smallint,timestamp with time zone,bigint,uuid)",
        _COMPLETO,
    ),
    (
        "app_private.accept_checkout_webhook(uuid,uuid,text,text,bigint,text,smallint,text,integer,text,text,smallint,text,smallint,timestamp with time zone,bigint,uuid,text,text)",
        _COMPLETO,
    ),
    ("app_private.active_learning_asset(uuid,uuid,uuid,uuid)", _COMPLETO),
    ("app_private.active_learning_entitlement(uuid,uuid,uuid,uuid,uuid)", _COMPLETO),
    ("app_private.advance_entitlements_to(uuid,uuid,uuid,uuid,uuid,text)", _COMPLETO),
    ("app_private.apply_checkout_fulfillment(uuid,text,uuid,uuid,bigint,uuid,text)", _COMPLETO),
    ("app_private.apply_demo_checkout_entitlement(uuid,text,uuid,uuid,bigint,uuid)", _COMPLETO),
    ("app_private.apply_request_context(uuid,uuid,text,bigint,bigint,text)", _PRIVADO),
    (
        "app_private.archive_course_lesson(uuid,uuid,uuid,uuid,uuid,uuid,bigint,uuid,text,uuid)",
        _COMPLETO,
    ),
    (
        "app_private.archive_course_module(uuid,uuid,uuid,uuid,uuid,bigint,uuid,text,uuid)",
        _COMPLETO,
    ),
    (
        "app_private.archive_lesson_asset(uuid,uuid,uuid,uuid,uuid,uuid,uuid,bigint,uuid,text,uuid)",
        _COMPLETO,
    ),
    (
        "app_private.asset_mutation_replay(uuid,text,uuid,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid)",
        _PRIVADO,
    ),
    ("app_private.asset_worker_context_allowed(uuid)", _PRIVADO),
    ("app_private.authorize_entitled_pdf_manifest(uuid,uuid,uuid)", _COMPLETO),
    ("app_private.authorize_entitled_pdf_page(uuid,uuid,uuid,integer)", _COMPLETO),
    ("app_private.authorize_learning_asset(uuid,uuid,uuid)", _COMPLETO),
    ("app_private.authorize_pdf_study_asset(uuid,uuid,uuid,text)", _COMPLETO),
    ("app_private.authorize_publication_asset(uuid,uuid,uuid)", _COMPLETO),
    ("app_private.authorize_publication_asset_download(uuid,uuid,uuid)", _COMPLETO),
    ("app_private.begin_asset_verification(uuid,uuid,uuid,text,integer)", _COMPLETO),
    (
        "app_private.cancel_asset_upload(uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,text,uuid)",
        _COMPLETO,
    ),
    ("app_private.catalog_draft_read_allowed(uuid)", _COMPLETO),
    ("app_private.catalog_owner_action_allowed(uuid,text)", _COMPLETO),
    ("app_private.claim_asset_storage_cleanup_jobs(uuid,text,integer,integer)", _PRIVADO),
    ("app_private.claim_asset_verification_jobs(uuid,text,integer,integer)", _COMPLETO),
    ("app_private.claim_demo_checkout_jobs(uuid,text,integer,integer)", _COMPLETO),
    ("app_private.claim_email_outbox(uuid,text,integer,integer)", _COMPLETO),
    ("app_private.claim_fulfillment_jobs(uuid,text,integer,integer)", _COMPLETO),
    ("app_private.clear_product_cover(uuid,uuid,uuid,bigint,uuid,text,uuid)", _COMPLETO),
    ("app_private.clear_product_image(uuid,uuid,uuid,text,bigint,uuid,text,uuid)", _COMPLETO),
    ("app_private.close_product_cover_upload(uuid,uuid,uuid,uuid,text,text,uuid)", _COMPLETO),
    ("app_private.close_product_image_upload(uuid,uuid,uuid,text,uuid,text,text,uuid)", _COMPLETO),
    ("app_private.complete_asset_storage_cleanup(uuid,text,uuid,uuid)", _PRIVADO),
    ("app_private.complete_demo_zero_checkout(uuid,text,uuid,text,uuid)", _COMPLETO),
    ("app_private.complete_email_outbox(uuid,text,uuid,uuid,bigint,text,uuid)", _COMPLETO),
    (
        "app_private.confirm_asset_upload(uuid,uuid,uuid,uuid,uuid,uuid,uuid,text,bigint,text,text,uuid,text,uuid)",
        _COMPLETO,
    ),
    ("app_private.consume_bff_request(uuid,bigint)", _PRIVADO),
    (
        "app_private.consume_superadmin_bootstrap(uuid,text,text,boolean,text,text,text,text,uuid,text,bigint,bigint,text)",
        _COMPLETO,
    ),
    ("app_private.context_text(text)", _PRIVADO),
    ("app_private.context_uuid(text)", _PRIVADO),
    (
        "app_private.create_asset_upload_intent(uuid,uuid,uuid,uuid,uuid,uuid,uuid,bigint,text,text,text,bigint,text,text,text,text,uuid,text,uuid)",
        _COMPLETO,
    ),
    (
        "app_private.create_course_lesson(uuid,uuid,uuid,uuid,uuid,text,text,bigint,uuid,text,uuid)",
        _COMPLETO,
    ),
    ("app_private.create_course_module(uuid,uuid,uuid,uuid,text,bigint,uuid,text,uuid)", _COMPLETO),
    (
        "app_private.create_product_course_draft(uuid,uuid,text,text,text,text,uuid,text,uuid)",
        _COMPLETO,
    ),
    (
        "app_private.create_product_cover_upload_intent(uuid,uuid,uuid,text,bigint,text,text,text,uuid,text,uuid)",
        _COMPLETO,
    ),
    (
        "app_private.create_product_image_upload_intent(uuid,uuid,uuid,text,text,bigint,text,text,text,uuid,text,uuid)",
        _COMPLETO,
    ),
    ("app_private.curriculum_action_allowed(uuid,uuid,text)", _COMPLETO),
    ("app_private.curriculum_content_bytes(uuid,uuid)", _PUBLICO),
    ("app_private.curriculum_courses_snapshot(uuid,uuid)", _PUBLICO),
    ("app_private.curriculum_replay(uuid,text,uuid,uuid,text,uuid,uuid,text)", _PRIVADO),
    ("app_private.enforce_asset_pdf_page_limits()", _PUBLICO),
    ("app_private.enforce_asset_pdf_raster_source()", _PUBLICO),
    ("app_private.enforce_lesson_asset_draft_policy()", _PUBLICO),
    ("app_private.enforce_publication_lesson_asset_limit()", _PUBLICO),
    ("app_private.enforce_s3_publication_pdf_raster()", _PUBLICO),
    ("app_private.enqueue_asset_storage_cleanup(uuid,uuid,text,text,text,text,text)", _PRIVADO),
    ("app_private.ensure_email_worker(uuid,text,text,text,text)", _COMPLETO),
    ("app_private.ensure_fulfillment_worker(uuid,text,text,text,text)", _COMPLETO),
    ("app_private.fail_demo_checkout_job(uuid,text,uuid,uuid,bigint,text,integer)", _PRIVADO),
    (
        "app_private.fail_email_outbox(uuid,text,uuid,uuid,bigint,text,integer,boolean,uuid)",
        _COMPLETO,
    ),
    ("app_private.fail_fulfillment_job(uuid,text,uuid,uuid,bigint,text,integer)", _PRIVADO),
    (
        "app_private.finalize_asset_verification(uuid,uuid,uuid,text,bigint,text,bigint,text,text,text,text,text,text,text,uuid,text,uuid)",
        _COMPLETO,
    ),
    (
        "app_private.finalize_asset_verification_v1_legacy(uuid,uuid,uuid,text,bigint,text,bigint,text,text,text,text,text,text,text,uuid,text,uuid)",
        _COMPLETO,
    ),
    (
        "app_private.finalize_asset_verification_v2(uuid,uuid,uuid,text,bigint,text,bigint,text,text,text,text,text,text,text,uuid,text,uuid,text)",
        _COMPLETO,
    ),
    ("app_private.identity_user_id(text,text)", _PUBLICO),
    ("app_private.learning_context_allowed(uuid,text)", _PRIVADO),
    ("app_private.legacy_asset_blob_id(uuid)", _CATALOGO),
    ("app_private.library_context_allowed(uuid,text)", _COMPLETO),
    ("app_private.list_lesson_asset_drafts(uuid,uuid,uuid,uuid,uuid,uuid)", _COMPLETO),
    ("app_private.list_lesson_asset_drafts_v2(uuid,uuid,uuid,uuid,uuid,uuid)", _COMPLETO),
    ("app_private.lock_active_superadmin(uuid,text)", _PUBLICO),
    ("app_private.lock_curriculum_access(uuid,uuid,uuid,uuid,text)", _COMPLETO),
    ("app_private.lock_curriculum_course(uuid,uuid,bigint)", _PUBLICO),
    ("app_private.mutate_pdf_study_state(uuid,uuid,uuid,bigint,uuid,text,uuid,jsonb)", _COMPLETO),
    ("app_private.onboard_seller(uuid,text,text,uuid,text,uuid)", _COMPLETO),
    ("app_private.product_student_counts(uuid,uuid)", _COMPLETO),
    ("app_private.project_pdf_study_state(uuid,uuid,uuid,uuid,integer,integer)", _COMPLETO),
    ("app_private.project_publication(uuid,uuid,uuid,jsonb)", _COMPLETO),
    ("app_private.project_publication_trigger()", _COMPLETO),
    (
        "app_private.publish_product(uuid,uuid,uuid,uuid,bigint,bigint,jsonb,uuid,text,uuid)",
        _COMPLETO,
    ),
    ("app_private.read_asset_upload_for_signing(uuid,uuid,uuid,uuid,uuid,uuid,uuid)", _COMPLETO),
    ("app_private.read_asset_upload_status(uuid,uuid,uuid,uuid,uuid,uuid,uuid)", _COMPLETO),
    ("app_private.read_buyer_library(uuid)", _COMPLETO),
    ("app_private.read_buyer_products(uuid)", _COMPLETO),
    ("app_private.read_buyer_seller_storefront(uuid,uuid)", _COMPLETO),
    ("app_private.read_course_curriculum(uuid,uuid,uuid,uuid)", _COMPLETO),
    ("app_private.read_demo_checkout(uuid)", _COMPLETO),
    ("app_private.read_demo_inspection(uuid,text,uuid,text,uuid)", _COMPLETO),
    ("app_private.read_learning_player(uuid,uuid,uuid)", _COMPLETO),
    ("app_private.read_learning_player_v2(uuid,uuid,uuid)", _COMPLETO),
    ("app_private.read_learning_player_v3(uuid,uuid,uuid)", _COMPLETO),
    ("app_private.read_lesson_asset_uploads(uuid,uuid,uuid,uuid,uuid,uuid)", _COMPLETO),
    ("app_private.read_pdf_study_state(uuid,uuid,uuid)", _COMPLETO),
    ("app_private.read_product_cover_upload(uuid,uuid,uuid,uuid)", _COMPLETO),
    ("app_private.read_product_image_upload(uuid,uuid,uuid,text,uuid)", _COMPLETO),
    ("app_private.read_public_storefront(text)", _COMPLETO),
    (
        "app_private.record_asset_mutation(uuid,text,uuid,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid,jsonb)",
        _PRIVADO,
    ),
    (
        "app_private.record_curriculum_mutation(uuid,text,uuid,uuid,text,uuid,uuid,text,jsonb)",
        _PRIVADO,
    ),
    (
        "app_private.record_lesson_progress(uuid,uuid,uuid,smallint,bigint,uuid,text,uuid)",
        _COMPLETO,
    ),
    (
        "app_private.register_asset_ready_candidate(uuid,uuid,uuid,text,text,text,bigint,text,text,text,text)",
        _COMPLETO,
    ),
    (
        "app_private.register_asset_ready_candidate_v1_legacy(uuid,uuid,uuid,text,text,text,bigint,text,text,text,text)",
        _COMPLETO,
    ),
    (
        "app_private.register_asset_ready_candidate_v2(uuid,uuid,uuid,text,text,text,bigint,text,text,text,text,jsonb)",
        _COMPLETO,
    ),
    ("app_private.reject_demo_commerce_mutation()", _CATALOGO),
    ("app_private.reject_member_superadmin_grant()", _PUBLICO),
    ("app_private.reject_superadmin_membership()", _PUBLICO),
    (
        "app_private.reorder_course_modules(uuid,uuid,uuid,uuid,uuid[],bigint,uuid,text,uuid)",
        _COMPLETO,
    ),
    (
        "app_private.reorder_module_lessons(uuid,uuid,uuid,uuid,uuid,uuid[],bigint,uuid,text,uuid)",
        _COMPLETO,
    ),
    (
        "app_private.replay_asset_upload_confirmation(uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,text)",
        _COMPLETO,
    ),
    ("app_private.resolve_webhook_endpoint(text)", _COMPLETO),
    ("app_private.resolve_webhook_endpoint(text,uuid)", _COMPLETO),
    ("app_private.retry_asset_storage_cleanup(uuid,text,uuid,uuid,integer,text)", _PRIVADO),
    ("app_private.revoke_demo_inspection(uuid,text,uuid,text,text,uuid)", _COMPLETO),
    ("app_private.schema_ready()", _PRIVADO),
    ("app_private.schema_ready_base_0016()", _PRIVADO),
    ("app_private.schema_ready_base_0017()", _PRIVADO),
    (
        "app_private.set_lesson_asset_policy(uuid,uuid,uuid,uuid,uuid,uuid,uuid,text,boolean,bigint,uuid,text,uuid)",
        _COMPLETO,
    ),
    (
        "app_private.set_product_cover(uuid,uuid,uuid,uuid,text,text,text,text,integer,integer,bigint,bigint,uuid,text,uuid)",
        _COMPLETO,
    ),
    (
        "app_private.set_product_image(uuid,uuid,uuid,text,uuid,text,text,text,text,integer,integer,bigint,bigint,uuid,text,uuid)",
        _COMPLETO,
    ),
    ("app_private.start_demo_inspection(uuid,text,text,integer,text,uuid)", _COMPLETO),
    ("app_private.superadmin_demo_context_allowed(uuid,text,text)", _COMPLETO),
    ("app_private.sync_identity(uuid,text,text,text,boolean)", _COMPLETO),
    ("app_private.sync_identity(uuid,text,text,text,boolean,text)", _COMPLETO),
    ("app_private.sync_learning_enrollments()", _COMPLETO),
    (
        "app_private.transition_offer_status(uuid,uuid,uuid,uuid,text,text,bigint,uuid,text,uuid)",
        _COMPLETO,
    ),
    (
        "app_private.update_course_lesson(uuid,uuid,uuid,uuid,uuid,uuid,text,text,bigint,uuid,text,uuid)",
        _COMPLETO,
    ),
    (
        "app_private.update_course_module(uuid,uuid,uuid,uuid,uuid,text,bigint,uuid,text,uuid)",
        _COMPLETO,
    ),
    (
        "app_private.update_product_course_draft(uuid,uuid,uuid,text,uuid,text,text,text,bigint,bigint,uuid,text,uuid)",
        _COMPLETO,
    ),
    (
        "app_private.update_product_course_offer_draft(uuid,uuid,uuid,text,uuid,uuid,text,text,text,text,text,bigint,text,text,bigint,bigint,bigint,uuid,text,uuid)",
        _COMPLETO,
    ),
    ("app_private.verified_context()", _COMPLETO),
)


def _carregar(revisao: str) -> ModuleType:
    caminho = Path(__file__).with_name(f"{revisao}.py")
    spec = importlib_util.spec_from_file_location(f"appsell_anterior_{revisao}", caminho)
    if spec is None or spec.loader is None:  # pragma: no cover - caminho fixo no repo
        raise RuntimeError(f"a migration {revisao} nao pode ser lida")
    modulo = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


# A lista do readiness vem da anterior na cadeia (`_READY_ATE_0072`).
READY_ANTES: str = _carregar(down_revision)._READY_ATE_0072
READY_DEPOIS = READY_ANTES[:-2] + f",'{revision}'))"
# Nome que as migrations seguintes leem por `getattr`.
_READY_ATE_0079 = READY_DEPOIS
_BASES_DO_READY: tuple[str, ...] = (
    "app_private.schema_ready_base_0016()",
    "app_private.schema_ready_base_0017()",
)

_REESCREVE = """
do $migration$
declare definicao text; reescrita text;
begin
  definicao := pg_get_functiondef('ALVO'::regprocedure);
  if (length(definicao)-length(replace(definicao,$de$DE$de$,''))) / length($de$DE$de$) <> 1 then
    raise exception 'ALVO nao continha a ancora exatamente uma vez' using errcode='55000';
  end if;
  reescrita := replace(definicao,$de$DE$de$,$para$PARA$para$);
  execute reescrita;
end $migration$
"""

# Confere o valor atual e só então troca. O `raise` diz a assinatura, o valor
# esperado e o encontrado, para um abort no deploy não virar caça no escuro.
_CONFERE_E_TROCA = """
do $migration$
declare atual text;
begin
  select substr(config, 13) into atual
    from pg_proc proc, unnest(proc.proconfig) config
   where proc.oid = 'ASSINATURA'::regprocedure and config like 'search_path=%';
  if atual is distinct from 'ESPERADO' then
    raise exception 'search_path divergiu em ASSINATURA: esperado "ESPERADO", encontrado "%"',
      coalesce(atual, '<sem search_path>') using errcode = '55000';
  end if;
  alter function ASSINATURA set search_path = NOVO;
end $migration$
"""


def _reescreve(alvo: str, de: str, para: str) -> str:
    return _REESCREVE.replace("ALVO", alvo).replace("DE", de).replace("PARA", para)


def confere_e_troca(assinatura: str, esperado: str, novo: str) -> str:
    return (
        _CONFERE_E_TROCA.replace("ASSINATURA", assinatura)
        .replace("ESPERADO", esperado)
        .replace("NOVO", novo)
    )


def upgrade() -> None:
    for assinatura, antes in FUNCOES:
        op.execute(confere_e_troca(assinatura, antes, antes + PG_TEMP))
    for alvo in _BASES_DO_READY:
        op.execute(_reescreve(alvo, READY_ANTES, READY_DEPOIS))


def downgrade() -> None:
    for alvo in _BASES_DO_READY:
        op.execute(_reescreve(alvo, READY_DEPOIS, READY_ANTES))
    for assinatura, antes in FUNCOES:
        op.execute(confere_e_troca(assinatura, antes + PG_TEMP, antes))
