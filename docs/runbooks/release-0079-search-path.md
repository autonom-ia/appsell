# Release da 0079: `pg_temp` no fim do `search_path` (#598)

A migration `0079_pg_temp_nas_funcoes` acrescenta `, pg_temp` ao `search_path`
de 129 funções `security definer` de `app_private`, por `alter function ... set
search_path`. Corpo, dono, grant e comportamento não mudam. Antes de alterar,
cada função tem o valor atual conferido; se algum divergir, a migration aborta
dizendo a assinatura, o valor esperado e o encontrado, e nada é alterado (a
migration roda numa transação só).

## Antes do deploy: conferência só de leitura (Trilha 1)

No banco de produção, com um papel de leitura, rodar a consulta abaixo. Ela não
escreve nada. **Esperado: 0 linhas.** Cada linha devolvida é uma função cujo
valor atual não é o que a migration espera, e a migration abortaria nela.
Nesse caso, não fazer o deploy: levar a linha à Qualidade e à Orquestração.

A lista é a mesma da migration (`FUNCOES`), e o teste
`apps/api/tests/test_pg_temp_nas_funcoes_migration.py` reprova se as duas se
separarem.

```sql
with esperado(assinatura, antes) as (values
    ('app_private.accept_checkout_webhook(uuid,uuid,text,text,bigint,text,smallint,text,integer,text,text,smallint,text,smallint,timestamp with time zone,bigint,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.accept_checkout_webhook(uuid,uuid,text,text,bigint,text,smallint,text,integer,text,text,smallint,text,smallint,timestamp with time zone,bigint,uuid,text,text)', 'pg_catalog, app_private, public'),
    ('app_private.active_learning_asset(uuid,uuid,uuid,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.active_learning_entitlement(uuid,uuid,uuid,uuid,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.advance_entitlements_to(uuid,uuid,uuid,uuid,uuid,text)', 'pg_catalog, app_private, public'),
    ('app_private.apply_checkout_fulfillment(uuid,text,uuid,uuid,bigint,uuid,text)', 'pg_catalog, app_private, public'),
    ('app_private.apply_demo_checkout_entitlement(uuid,text,uuid,uuid,bigint,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.apply_request_context(uuid,uuid,text,bigint,bigint,text)', 'pg_catalog, app_private'),
    ('app_private.archive_course_lesson(uuid,uuid,uuid,uuid,uuid,uuid,bigint,uuid,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.archive_course_module(uuid,uuid,uuid,uuid,uuid,bigint,uuid,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.archive_lesson_asset(uuid,uuid,uuid,uuid,uuid,uuid,uuid,bigint,uuid,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.asset_mutation_replay(uuid,text,uuid,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid)', 'pg_catalog, app_private'),
    ('app_private.asset_worker_context_allowed(uuid)', 'pg_catalog, app_private'),
    ('app_private.authorize_entitled_pdf_manifest(uuid,uuid,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.authorize_entitled_pdf_page(uuid,uuid,uuid,integer)', 'pg_catalog, app_private, public'),
    ('app_private.authorize_learning_asset(uuid,uuid,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.authorize_pdf_study_asset(uuid,uuid,uuid,text)', 'pg_catalog, app_private, public'),
    ('app_private.authorize_publication_asset(uuid,uuid,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.authorize_publication_asset_download(uuid,uuid,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.begin_asset_verification(uuid,uuid,uuid,text,integer)', 'pg_catalog, app_private, public'),
    ('app_private.cancel_asset_upload(uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.catalog_draft_read_allowed(uuid)', 'pg_catalog, app_private, public'),
    ('app_private.catalog_owner_action_allowed(uuid,text)', 'pg_catalog, app_private, public'),
    ('app_private.claim_asset_storage_cleanup_jobs(uuid,text,integer,integer)', 'pg_catalog, app_private'),
    ('app_private.claim_asset_verification_jobs(uuid,text,integer,integer)', 'pg_catalog, app_private, public'),
    ('app_private.claim_demo_checkout_jobs(uuid,text,integer,integer)', 'pg_catalog, app_private, public'),
    ('app_private.claim_email_outbox(uuid,text,integer,integer)', 'pg_catalog, app_private, public'),
    ('app_private.claim_fulfillment_jobs(uuid,text,integer,integer)', 'pg_catalog, app_private, public'),
    ('app_private.clear_product_cover(uuid,uuid,uuid,bigint,uuid,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.clear_product_image(uuid,uuid,uuid,text,bigint,uuid,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.close_product_cover_upload(uuid,uuid,uuid,uuid,text,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.close_product_image_upload(uuid,uuid,uuid,text,uuid,text,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.complete_asset_storage_cleanup(uuid,text,uuid,uuid)', 'pg_catalog, app_private'),
    ('app_private.complete_demo_zero_checkout(uuid,text,uuid,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.complete_email_outbox(uuid,text,uuid,uuid,bigint,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.confirm_asset_upload(uuid,uuid,uuid,uuid,uuid,uuid,uuid,text,bigint,text,text,uuid,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.consume_bff_request(uuid,bigint)', 'pg_catalog, app_private'),
    ('app_private.consume_superadmin_bootstrap(uuid,text,text,boolean,text,text,text,text,uuid,text,bigint,bigint,text)', 'pg_catalog, app_private, public'),
    ('app_private.context_text(text)', 'pg_catalog, app_private'),
    ('app_private.context_uuid(text)', 'pg_catalog, app_private'),
    ('app_private.create_asset_upload_intent(uuid,uuid,uuid,uuid,uuid,uuid,uuid,bigint,text,text,text,bigint,text,text,text,text,uuid,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.create_course_lesson(uuid,uuid,uuid,uuid,uuid,text,text,bigint,uuid,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.create_course_module(uuid,uuid,uuid,uuid,text,bigint,uuid,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.create_product_course_draft(uuid,uuid,text,text,text,text,uuid,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.create_product_cover_upload_intent(uuid,uuid,uuid,text,bigint,text,text,text,uuid,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.create_product_image_upload_intent(uuid,uuid,uuid,text,text,bigint,text,text,text,uuid,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.curriculum_action_allowed(uuid,uuid,text)', 'pg_catalog, app_private, public'),
    ('app_private.curriculum_content_bytes(uuid,uuid)', 'pg_catalog, public'),
    ('app_private.curriculum_courses_snapshot(uuid,uuid)', 'pg_catalog, public'),
    ('app_private.curriculum_replay(uuid,text,uuid,uuid,text,uuid,uuid,text)', 'pg_catalog, app_private'),
    ('app_private.enforce_asset_pdf_page_limits()', 'pg_catalog, public'),
    ('app_private.enforce_asset_pdf_raster_source()', 'pg_catalog, public'),
    ('app_private.enforce_lesson_asset_draft_policy()', 'pg_catalog, public'),
    ('app_private.enforce_publication_lesson_asset_limit()', 'pg_catalog, public'),
    ('app_private.enforce_s3_publication_pdf_raster()', 'pg_catalog, public'),
    ('app_private.enqueue_asset_storage_cleanup(uuid,uuid,text,text,text,text,text)', 'pg_catalog, app_private'),
    ('app_private.ensure_email_worker(uuid,text,text,text,text)', 'pg_catalog, app_private, public'),
    ('app_private.ensure_fulfillment_worker(uuid,text,text,text,text)', 'pg_catalog, app_private, public'),
    ('app_private.fail_demo_checkout_job(uuid,text,uuid,uuid,bigint,text,integer)', 'pg_catalog, app_private'),
    ('app_private.fail_email_outbox(uuid,text,uuid,uuid,bigint,text,integer,boolean,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.fail_fulfillment_job(uuid,text,uuid,uuid,bigint,text,integer)', 'pg_catalog, app_private'),
    ('app_private.finalize_asset_verification(uuid,uuid,uuid,text,bigint,text,bigint,text,text,text,text,text,text,text,uuid,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.finalize_asset_verification_v1_legacy(uuid,uuid,uuid,text,bigint,text,bigint,text,text,text,text,text,text,text,uuid,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.finalize_asset_verification_v2(uuid,uuid,uuid,text,bigint,text,bigint,text,text,text,text,text,text,text,uuid,text,uuid,text)', 'pg_catalog, app_private, public'),
    ('app_private.identity_user_id(text,text)', 'pg_catalog, public'),
    ('app_private.learning_context_allowed(uuid,text)', 'pg_catalog, app_private'),
    ('app_private.legacy_asset_blob_id(uuid)', 'pg_catalog'),
    ('app_private.library_context_allowed(uuid,text)', 'pg_catalog, app_private, public'),
    ('app_private.list_lesson_asset_drafts(uuid,uuid,uuid,uuid,uuid,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.list_lesson_asset_drafts_v2(uuid,uuid,uuid,uuid,uuid,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.lock_active_superadmin(uuid,text)', 'pg_catalog, public'),
    ('app_private.lock_curriculum_access(uuid,uuid,uuid,uuid,text)', 'pg_catalog, app_private, public'),
    ('app_private.lock_curriculum_course(uuid,uuid,bigint)', 'pg_catalog, public'),
    ('app_private.mutate_pdf_study_state(uuid,uuid,uuid,bigint,uuid,text,uuid,jsonb)', 'pg_catalog, app_private, public'),
    ('app_private.onboard_seller(uuid,text,text,uuid,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.product_student_counts(uuid,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.project_pdf_study_state(uuid,uuid,uuid,uuid,integer,integer)', 'pg_catalog, app_private, public'),
    ('app_private.project_publication(uuid,uuid,uuid,jsonb)', 'pg_catalog, app_private, public'),
    ('app_private.project_publication_trigger()', 'pg_catalog, app_private, public'),
    ('app_private.publish_product(uuid,uuid,uuid,uuid,bigint,bigint,jsonb,uuid,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.read_asset_upload_for_signing(uuid,uuid,uuid,uuid,uuid,uuid,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.read_asset_upload_status(uuid,uuid,uuid,uuid,uuid,uuid,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.read_buyer_library(uuid)', 'pg_catalog, app_private, public'),
    ('app_private.read_buyer_products(uuid)', 'pg_catalog, app_private, public'),
    ('app_private.read_buyer_seller_storefront(uuid,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.read_course_curriculum(uuid,uuid,uuid,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.read_demo_checkout(uuid)', 'pg_catalog, app_private, public'),
    ('app_private.read_demo_inspection(uuid,text,uuid,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.read_learning_player(uuid,uuid,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.read_learning_player_v2(uuid,uuid,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.read_learning_player_v3(uuid,uuid,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.read_lesson_asset_uploads(uuid,uuid,uuid,uuid,uuid,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.read_pdf_study_state(uuid,uuid,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.read_product_cover_upload(uuid,uuid,uuid,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.read_product_image_upload(uuid,uuid,uuid,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.read_public_storefront(text)', 'pg_catalog, app_private, public'),
    ('app_private.record_asset_mutation(uuid,text,uuid,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid,jsonb)', 'pg_catalog, app_private'),
    ('app_private.record_curriculum_mutation(uuid,text,uuid,uuid,text,uuid,uuid,text,jsonb)', 'pg_catalog, app_private'),
    ('app_private.record_lesson_progress(uuid,uuid,uuid,smallint,bigint,uuid,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.register_asset_ready_candidate(uuid,uuid,uuid,text,text,text,bigint,text,text,text,text)', 'pg_catalog, app_private, public'),
    ('app_private.register_asset_ready_candidate_v1_legacy(uuid,uuid,uuid,text,text,text,bigint,text,text,text,text)', 'pg_catalog, app_private, public'),
    ('app_private.register_asset_ready_candidate_v2(uuid,uuid,uuid,text,text,text,bigint,text,text,text,text,jsonb)', 'pg_catalog, app_private, public'),
    ('app_private.reject_demo_commerce_mutation()', 'pg_catalog'),
    ('app_private.reject_member_superadmin_grant()', 'pg_catalog, public'),
    ('app_private.reject_superadmin_membership()', 'pg_catalog, public'),
    ('app_private.reorder_course_modules(uuid,uuid,uuid,uuid,uuid[],bigint,uuid,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.reorder_module_lessons(uuid,uuid,uuid,uuid,uuid,uuid[],bigint,uuid,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.replay_asset_upload_confirmation(uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,text)', 'pg_catalog, app_private, public'),
    ('app_private.resolve_webhook_endpoint(text)', 'pg_catalog, app_private, public'),
    ('app_private.resolve_webhook_endpoint(text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.retry_asset_storage_cleanup(uuid,text,uuid,uuid,integer,text)', 'pg_catalog, app_private'),
    ('app_private.revoke_demo_inspection(uuid,text,uuid,text,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.schema_ready()', 'pg_catalog, app_private'),
    ('app_private.schema_ready_base_0016()', 'pg_catalog, app_private'),
    ('app_private.schema_ready_base_0017()', 'pg_catalog, app_private'),
    ('app_private.set_lesson_asset_policy(uuid,uuid,uuid,uuid,uuid,uuid,uuid,text,boolean,bigint,uuid,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.set_product_cover(uuid,uuid,uuid,uuid,text,text,text,text,integer,integer,bigint,bigint,uuid,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.set_product_image(uuid,uuid,uuid,text,uuid,text,text,text,text,integer,integer,bigint,bigint,uuid,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.start_demo_inspection(uuid,text,text,integer,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.superadmin_demo_context_allowed(uuid,text,text)', 'pg_catalog, app_private, public'),
    ('app_private.sync_identity(uuid,text,text,text,boolean)', 'pg_catalog, app_private, public'),
    ('app_private.sync_identity(uuid,text,text,text,boolean,text)', 'pg_catalog, app_private, public'),
    ('app_private.sync_learning_enrollments()', 'pg_catalog, app_private, public'),
    ('app_private.transition_offer_status(uuid,uuid,uuid,uuid,text,text,bigint,uuid,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.update_course_lesson(uuid,uuid,uuid,uuid,uuid,uuid,text,text,bigint,uuid,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.update_course_module(uuid,uuid,uuid,uuid,uuid,text,bigint,uuid,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.update_product_course_draft(uuid,uuid,uuid,text,uuid,text,text,text,bigint,bigint,uuid,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.update_product_course_offer_draft(uuid,uuid,uuid,text,uuid,uuid,text,text,text,text,text,bigint,text,text,bigint,bigint,bigint,uuid,text,uuid)', 'pg_catalog, app_private, public'),
    ('app_private.verified_context()', 'pg_catalog, app_private, public')
),
atual as (
  select e.assinatura, e.antes,
         case
           when to_regprocedure(e.assinatura) is null then '<função não existe>'
           else coalesce(
             (select substr(config, 13)
                from pg_proc proc, unnest(proc.proconfig) config
               where proc.oid = to_regprocedure(e.assinatura)
                 and config like 'search_path=%'),
             '<sem search_path>')
         end as encontrado
    from esperado e
)
select assinatura, antes as esperado, encontrado
  from atual
 where encontrado is distinct from antes
 order by assinatura;
```

Conferência complementar, também só de leitura: quais funções `security
definer` ainda não terminam em `pg_temp`. Ela pega também uma função criada
fora das migrations, que a consulta acima não enxerga. **Esperado antes do
deploy: 129 linhas, todas na lista acima. Depois: 0 linhas.** Qualquer outro
número para o deploy: a linha a mais é uma função que a migration não conhece.

```sql
select proc.oid::regprocedure::text as assinatura
  from pg_proc proc
  join pg_namespace ns on ns.oid = proc.pronamespace
 where proc.prosecdef
   and ns.nspname not in ('pg_catalog', 'information_schema')
   and ns.nspname not like 'pg\_%'
   and coalesce((select substr(config, 13) from unnest(proc.proconfig) config
                  where config like 'search_path=%'), '') not like '%, pg_temp'
 order by 1;
```

## Ordem

**Migration antes do código.** A migration não muda contrato, mas a imagem da
api desta versão exige a 0079: `reset_demo_tenant`, `seed_capacity_tenants` e
`seed_buyer_multi_seller` conferem que `alembic_version` é exatamente
`0079_pg_temp_nas_funcoes`. Se a imagem subir antes, essas ferramentas de demo
e seed recusam rodar. A ordem final vai escrita no PR de release, como manda o
AGENTS.md §6.

## Depois do deploy

- `alembic current` = `0079_pg_temp_nas_funcoes` e `schema_ready` verdadeiro;
- a conferência complementar devolve 0 linhas;
- o smoke da Hub2You igual ao de antes (AGENTS.md §7).

## Volta

`alembic downgrade 0072_relatorio_de_vendas` (ou a revisão anterior na cadeia
do momento). O downgrade confere o valor de depois e devolve o de antes, função
por função; se alguma divergir, aborta dizendo qual. Não apaga dado nem muda
grant.
