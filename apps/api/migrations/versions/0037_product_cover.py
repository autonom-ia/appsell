"""Give a product a cover, and let the list say how many students it has.

Two things the catalogue could not say. A product had no image, so every card
looked the same and the seller had no way to make one look like theirs. And the
number of students was unreachable from the listing: `learning_enrollment` is
not readable by the runtime role, on purpose, so counting it required a function
that crosses that boundary deliberately instead of a join that quietly widens
it.

The cover columns are all-or-nothing. A half-written cover — a key with no
digest, a digest with no dimensions — would be a row describing a file nobody
can verify, and the check refuses it rather than trusting the writer to be
careful.

The digest stored on the product is the digest of the **sanitised** file. The
API rewrites every cover to strip metadata, so recording what the seller
uploaded would make this table describe something that is not what is stored.
The digest the seller declared stays on the upload row instead: there it is a
transport check against the bytes that arrived, which is the only thing it can
honestly prove.

A cover is 5 MiB and the signed internal envelope reads at most 64 KiB, so the
bytes cannot travel through the API at all. They go straight from the browser to
the quarantine bucket under a presigned PUT, and this table is the record of
what was asked for before any byte exists. The quarantine key is derived from
the product and never from the upload, so an abandoned attempt is overwritten by
the next one instead of accumulating: at most one unverified cover per product
can be sitting in quarantine at any moment.
"""

from __future__ import annotations

from alembic import op

revision = "0037_product_cover"
down_revision = "0036_lesson_material_links"
branch_labels = None
depends_on = None

_COLUMNS = r"""
alter table public.catalog_product
  add column cover_object_key text,
  add column cover_storage_version_id text,
  add column cover_media_type text,
  add column cover_sha256 text,
  add column cover_width integer,
  add column cover_height integer,
  add column cover_byte_size bigint,
  add column cover_updated_at timestamptz
"""

# Uma capa existe inteira ou nao existe. Nada de linha meio escrita.
_CHECK = r"""
alter table public.catalog_product
  add constraint catalog_product_cover_complete check (
    (cover_object_key is null and cover_storage_version_id is null
     and cover_media_type is null and cover_sha256 is null
     and cover_width is null and cover_height is null and cover_byte_size is null
     and cover_updated_at is null)
    or
    (cover_object_key is not null and cover_storage_version_id is not null
     and cover_media_type is not null
     and cover_sha256 is not null and cover_width is not null
     and cover_height is not null and cover_byte_size is not null
     and cover_updated_at is not null
     and cover_media_type in ('image/jpeg','image/png','image/webp')
     and cover_sha256 ~ '^[0-9a-f]{64}$'
     and cover_width between 64 and 4000
     and cover_height between 64 and 4000
     and cover_byte_size between 1 and 5242880
     and length(cover_object_key) between 1 and 400
     and length(cover_storage_version_id) between 1 and 512)
  )
"""

# O registro do que foi pedido antes de existir byte nenhum. Sem ele, um upload
# abandonado deixa lixo que ninguem sabe de quem e; com ele, e possivel dizer
# que aquele objeto na quarentena pertence a este produto e ja passou da hora.
_UPLOAD_TABLE = r"""
create table app_private.product_cover_upload (
  seller_organization_id uuid not null,
  id uuid not null,
  identity_user_id uuid not null references public.identity_user(id),
  catalog_product_id uuid not null,
  expected_media_type text not null check(
    expected_media_type in ('image/jpeg','image/png','image/webp')
  ),
  expected_byte_size bigint not null check(expected_byte_size between 1 and 5242880),
  expected_sha256 text not null check(expected_sha256 ~ '^[0-9a-f]{64}$'),
  quarantine_bucket text not null check(
    quarantine_bucket = btrim(quarantine_bucket)
    and octet_length(quarantine_bucket) between 3 and 255
    and quarantine_bucket ~ '^[a-z0-9][a-z0-9.-]+$'
  ),
  ready_bucket text not null check(
    ready_bucket = btrim(ready_bucket)
    and octet_length(ready_bucket) between 3 and 255
    and ready_bucket ~ '^[a-z0-9][a-z0-9.-]+$'
  ),
  quarantine_key text not null check(octet_length(quarantine_key) between 1 and 400),
  ready_key text not null check(octet_length(ready_key) between 1 and 400),
  status text not null check(status in ('pending','ready','rejected','cancelled','expired')),
  failure_code text check(
    failure_code is null or octet_length(failure_code) between 2 and 80
  ),
  expires_at timestamptz not null,
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp(),
  primary key(seller_organization_id,id),
  constraint product_cover_upload_product_fkey
    foreign key(seller_organization_id,catalog_product_id)
    references public.catalog_product(seller_organization_id,id),
  constraint product_cover_upload_bucket_separation check(quarantine_bucket <> ready_bucket),
  constraint product_cover_upload_expiry check(expires_at > created_at),
  constraint product_cover_upload_ready_key unique(ready_bucket,ready_key)
)
"""

# No maximo um upload de capa aberto por produto. E o que torna a chave de
# quarentena deterministica segura: duas tentativas simultaneas nao existem.
_UPLOAD_PENDING_UNIQUE = r"""
create unique index product_cover_upload_one_pending_idx
  on app_private.product_cover_upload(seller_organization_id,catalog_product_id)
  where status = 'pending'
"""

_UPLOAD_STATUS_INDEX = r"""
create index product_cover_upload_expiry_idx
  on app_private.product_cover_upload(seller_organization_id,status,expires_at)
"""

# A autorizacao e a mesma das outras funcoes de catalogo: o contexto da
# transacao, posto por apply_request_context e verificado aqui. Escrito por
# extenso em cada funcao em vez de montado por concatenacao, como no resto das
# migrations — SQL neste repositorio e literal, e literal e o que se le.
_CREATE_INTENT = r"""
create function app_private.create_product_cover_upload_intent(
  p_identity_user_id uuid, p_seller_organization_id uuid, p_catalog_product_id uuid,
  p_media_type text, p_byte_size bigint, p_sha256 text,
  p_quarantine_bucket text, p_ready_bucket text,
  p_idempotency_key uuid, p_body_sha256 text, p_request_id uuid
) returns jsonb
language plpgsql security definer set search_path = pg_catalog, app_private, public as $$
declare
  locked_product public.catalog_product%rowtype;
  upload_id uuid := gen_random_uuid();
  quarantine text;
  ready text;
  expires timestamptz;
begin
  if app_private.context_text('capability') <> 'catalog.draft.update'
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id') is distinct from
        p_seller_organization_id then
    raise exception 'product cover context is not authorized' using errcode = '28000';
  end if;

  if p_media_type is null
     or p_media_type not in ('image/jpeg','image/png','image/webp')
     or p_byte_size is null or p_byte_size not between 1 and 5242880
     or p_sha256 is null or p_sha256 !~ '^[0-9a-f]{64}$'
     or p_quarantine_bucket is null or p_ready_bucket is null
     or p_quarantine_bucket = p_ready_bucket
     or p_idempotency_key is null or p_request_id is null
     or p_body_sha256 is null or p_body_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception 'invalid product cover upload intent' using errcode = '22023';
  end if;

  select * into locked_product from public.catalog_product product
  where product.seller_organization_id = p_seller_organization_id
    and product.id = p_catalog_product_id
  for update;
  if not found then
    raise exception 'catalog product not found' using errcode = 'P4041';
  end if;
  if locked_product.status = 'archived' then
    raise exception 'archived product cannot take a cover' using errcode = 'P4228';
  end if;

  -- Uma tentativa anterior deste mesmo produto e substituida, nao enfileirada.
  -- Repetir o pedido e seguro por isto, e nao por deduplicacao de chave: o
  -- vendedor que fechou o seletor de arquivo nao fica travado ate expirar.
  update app_private.product_cover_upload attempt
  set status = case when attempt.expires_at <= clock_timestamp() then 'expired' else 'cancelled' end,
      failure_code = 'superseded',
      updated_at = clock_timestamp()
  where attempt.seller_organization_id = p_seller_organization_id
    and attempt.catalog_product_id = p_catalog_product_id
    and attempt.status = 'pending';

  quarantine := 'tenants/' || p_seller_organization_id::text
    || '/quarantine/cover/' || p_catalog_product_id::text;
  ready := 'tenants/' || p_seller_organization_id::text
    || '/covers/' || p_catalog_product_id::text || '/' || upload_id::text;
  expires := clock_timestamp() + interval '15 minutes';

  insert into app_private.product_cover_upload(
    seller_organization_id,id,identity_user_id,catalog_product_id,
    expected_media_type,expected_byte_size,expected_sha256,
    quarantine_bucket,ready_bucket,quarantine_key,ready_key,status,expires_at
  ) values(
    p_seller_organization_id,upload_id,p_identity_user_id,p_catalog_product_id,
    p_media_type,p_byte_size,p_sha256,
    p_quarantine_bucket,p_ready_bucket,quarantine,ready,'pending',expires
  );

  insert into public.audit_event(
    seller_organization_id,actor_identity_user_id,action,target_type,target_id,
    result,request_id,metadata
  ) values(
    p_seller_organization_id,p_identity_user_id,'catalog.product_cover_upload_created',
    'catalog_product',p_catalog_product_id,'succeeded',p_request_id,
    jsonb_build_object(
      'upload_id',upload_id,'media_type',p_media_type,'byte_size',p_byte_size,
      'idempotency_key',p_idempotency_key
    )
  );

  return jsonb_build_object(
    'upload_id',upload_id,'quarantine_bucket',p_quarantine_bucket,
    'quarantine_key',quarantine,'ready_key',ready,
    'media_type',p_media_type,'byte_size',p_byte_size,'sha256',p_sha256,
    'expires_at',expires,'product_version',locked_product.version
  );
end $$
"""

_READ_UPLOAD = r"""
create function app_private.read_product_cover_upload(
  p_identity_user_id uuid, p_seller_organization_id uuid, p_catalog_product_id uuid,
  p_upload_id uuid
) returns jsonb
language plpgsql security definer set search_path = pg_catalog, app_private, public as $$
declare found_upload app_private.product_cover_upload%rowtype;
begin
  if app_private.context_text('capability') <> 'catalog.draft.update'
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id') is distinct from
        p_seller_organization_id then
    raise exception 'product cover context is not authorized' using errcode = '28000';
  end if;

  select * into found_upload from app_private.product_cover_upload attempt
  where attempt.seller_organization_id = p_seller_organization_id
    and attempt.catalog_product_id = p_catalog_product_id
    and attempt.id = p_upload_id;
  if not found then
    raise exception 'product cover upload not found' using errcode = 'P4041';
  end if;

  return jsonb_build_object(
    'upload_id',found_upload.id,'status',found_upload.status,
    'quarantine_bucket',found_upload.quarantine_bucket,
    'quarantine_key',found_upload.quarantine_key,
    'ready_bucket',found_upload.ready_bucket,'ready_key',found_upload.ready_key,
    'media_type',found_upload.expected_media_type,
    'byte_size',found_upload.expected_byte_size,'sha256',found_upload.expected_sha256,
    'expires_at',found_upload.expires_at,'failure_code',found_upload.failure_code,
    'expired',found_upload.expires_at <= clock_timestamp()
  );
end $$
"""

# Fechar um upload que nao virou capa. Um so caminho para recusa, cancelamento
# e expiracao, porque os tres terminam no mesmo lugar: linha fechada, motivo
# registrado, e o objeto na quarentena livre para ser apagado.
_CLOSE_UPLOAD = r"""
create function app_private.close_product_cover_upload(
  p_identity_user_id uuid, p_seller_organization_id uuid, p_catalog_product_id uuid,
  p_upload_id uuid, p_status text, p_failure_code text, p_request_id uuid
) returns jsonb
language plpgsql security definer set search_path = pg_catalog, app_private, public as $$
declare closed app_private.product_cover_upload%rowtype;
begin
  if app_private.context_text('capability') <> 'catalog.draft.update'
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id') is distinct from
        p_seller_organization_id then
    raise exception 'product cover context is not authorized' using errcode = '28000';
  end if;
  if p_status is null or p_status not in ('rejected','cancelled','expired')
     or p_request_id is null
     or (p_failure_code is not null
         and octet_length(p_failure_code) not between 2 and 80) then
    raise exception 'invalid product cover upload closure' using errcode = '22023';
  end if;

  select * into closed from app_private.product_cover_upload attempt
  where attempt.seller_organization_id = p_seller_organization_id
    and attempt.catalog_product_id = p_catalog_product_id
    and attempt.id = p_upload_id
  for update;
  if not found then
    raise exception 'product cover upload not found' using errcode = 'P4041';
  end if;
  -- Fechar duas vezes chega ao mesmo estado que fechar uma. Um clique repetido
  -- ou uma retentativa do worker nao viram erro.
  if closed.status <> 'pending' then
    return jsonb_build_object('upload_id',closed.id,'status',closed.status,'changed',false);
  end if;

  update app_private.product_cover_upload attempt
  set status = p_status, failure_code = p_failure_code, updated_at = clock_timestamp()
  where attempt.seller_organization_id = p_seller_organization_id
    and attempt.id = p_upload_id;

  insert into public.audit_event(
    seller_organization_id,actor_identity_user_id,action,target_type,target_id,
    result,request_id,metadata
  ) values(
    p_seller_organization_id,p_identity_user_id,'catalog.product_cover_upload_closed',
    'catalog_product',p_catalog_product_id,
    case when p_status = 'rejected' then 'failed' else 'succeeded' end,
    p_request_id,
    jsonb_build_object('upload_id',p_upload_id,'status',p_status,'failure_code',p_failure_code)
  );

  return jsonb_build_object('upload_id',p_upload_id,'status',p_status,'changed',true);
end $$
"""

_SET_COVER = r"""
create function app_private.set_product_cover(
  p_identity_user_id uuid, p_seller_organization_id uuid, p_catalog_product_id uuid,
  p_upload_id uuid, p_object_key text, p_storage_version_id text,
  p_media_type text, p_sha256 text,
  p_width integer, p_height integer, p_byte_size bigint,
  p_expected_product_version bigint, p_idempotency_key uuid,
  p_body_sha256 text, p_request_id uuid
) returns table(product_version bigint, previous_object_key text)
language plpgsql security definer set search_path = pg_catalog, app_private, public as $$
declare
  locked_product public.catalog_product%rowtype;
  locked_upload app_private.product_cover_upload%rowtype;
begin
  if app_private.context_text('capability') <> 'catalog.draft.update'
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id') is distinct from
        p_seller_organization_id then
    raise exception 'product cover context is not authorized' using errcode = '28000';
  end if;

  if p_object_key is null or octet_length(p_object_key) not between 1 and 400
     or p_storage_version_id is null
     or octet_length(p_storage_version_id) not between 1 and 512
     or p_media_type is null
     or p_media_type not in ('image/jpeg','image/png','image/webp')
     or p_sha256 is null or p_sha256 !~ '^[0-9a-f]{64}$'
     or p_width is null or p_width not between 64 and 4000
     or p_height is null or p_height not between 64 and 4000
     or p_byte_size is null or p_byte_size not between 1 and 5242880
     or p_expected_product_version is null or p_expected_product_version < 1
     or p_upload_id is null
     or p_idempotency_key is null or p_request_id is null
     or p_body_sha256 is null or p_body_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception 'invalid product cover request' using errcode = '22023';
  end if;

  select * into locked_upload from app_private.product_cover_upload attempt
  where attempt.seller_organization_id = p_seller_organization_id
    and attempt.catalog_product_id = p_catalog_product_id
    and attempt.id = p_upload_id
  for update;
  if not found then
    raise exception 'product cover upload not found' using errcode = 'P4041';
  end if;
  if locked_upload.status <> 'pending' then
    raise exception 'product cover upload is closed' using errcode = 'P4229';
  end if;
  -- A chave e a do intent, nunca a que o chamador quis. Sem isto, quem falasse
  -- com esta funcao poderia apontar a capa para qualquer objeto do bucket.
  if locked_upload.ready_key <> p_object_key then
    raise exception 'product cover key is not the reserved one' using errcode = '28000';
  end if;
  if locked_upload.expected_media_type <> p_media_type then
    raise exception 'product cover media type differs from the upload intent'
      using errcode = '22023';
  end if;

  select * into locked_product from public.catalog_product product
  where product.seller_organization_id = p_seller_organization_id
    and product.id = p_catalog_product_id
  for update;
  if not found then
    raise exception 'catalog product not found' using errcode = 'P4041';
  end if;
  if locked_product.status = 'archived' then
    raise exception 'archived product cannot take a cover' using errcode = 'P4228';
  end if;
  if locked_product.version <> p_expected_product_version then
    raise exception 'catalog product version conflict' using errcode = '40001';
  end if;

  previous_object_key := locked_product.cover_object_key;

  update public.catalog_product product
  set cover_object_key = p_object_key,
      cover_storage_version_id = p_storage_version_id,
      cover_media_type = p_media_type,
      cover_sha256 = p_sha256,
      cover_width = p_width,
      cover_height = p_height,
      cover_byte_size = p_byte_size,
      cover_updated_at = now(),
      version = product.version + 1,
      updated_at = now()
  where product.seller_organization_id = p_seller_organization_id
    and product.id = p_catalog_product_id
  returning product.version into product_version;

  update app_private.product_cover_upload attempt
  set status = 'ready', updated_at = clock_timestamp()
  where attempt.seller_organization_id = p_seller_organization_id
    and attempt.id = p_upload_id;

  insert into public.audit_event(
    seller_organization_id,actor_identity_user_id,action,target_type,target_id,
    result,request_id,metadata
  ) values(
    p_seller_organization_id,p_identity_user_id,'catalog.product_cover_set',
    'catalog_product',p_catalog_product_id,'succeeded',p_request_id,
    jsonb_build_object(
      'upload_id',p_upload_id,'media_type',p_media_type,'byte_size',p_byte_size,
      'width',p_width,'height',p_height,'idempotency_key',p_idempotency_key
    )
  );
  return next;
end $$
"""

_CLEAR_COVER = r"""
create function app_private.clear_product_cover(
  p_identity_user_id uuid, p_seller_organization_id uuid, p_catalog_product_id uuid,
  p_expected_product_version bigint, p_idempotency_key uuid,
  p_body_sha256 text, p_request_id uuid
) returns table(product_version bigint, changed boolean, previous_object_key text)
language plpgsql security definer set search_path = pg_catalog, app_private, public as $$
declare locked_product public.catalog_product%rowtype;
begin
  if app_private.context_text('capability') <> 'catalog.draft.update'
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id') is distinct from
        p_seller_organization_id then
    raise exception 'product cover context is not authorized' using errcode = '28000';
  end if;
  if p_idempotency_key is null or p_request_id is null
     or p_body_sha256 is null or p_body_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception 'invalid product cover request' using errcode = '22023';
  end if;

  select * into locked_product from public.catalog_product product
  where product.seller_organization_id = p_seller_organization_id
    and product.id = p_catalog_product_id
  for update;
  if not found then
    raise exception 'catalog product not found' using errcode = 'P4041';
  end if;
  -- Remover uma capa que ja nao existe e sucesso, nao erro: a segunda chamada
  -- de um clique repetido chega ao mesmo estado que a primeira.
  if locked_product.cover_object_key is null then
    product_version := locked_product.version;
    changed := false;
    previous_object_key := null;
    return next;
    return;
  end if;
  if locked_product.version <> p_expected_product_version then
    raise exception 'catalog product version conflict' using errcode = '40001';
  end if;

  previous_object_key := locked_product.cover_object_key;

  update public.catalog_product product
  set cover_object_key = null, cover_storage_version_id = null,
      cover_media_type = null, cover_sha256 = null,
      cover_width = null, cover_height = null, cover_byte_size = null,
      cover_updated_at = null,
      version = product.version + 1, updated_at = now()
  where product.seller_organization_id = p_seller_organization_id
    and product.id = p_catalog_product_id
  returning product.version into product_version;
  changed := true;

  insert into public.audit_event(
    seller_organization_id,actor_identity_user_id,action,target_type,target_id,
    result,request_id,metadata
  ) values(
    p_seller_organization_id,p_identity_user_id,'catalog.product_cover_cleared',
    'catalog_product',p_catalog_product_id,'succeeded',p_request_id,
    jsonb_build_object('idempotency_key',p_idempotency_key)
  );
  return next;
end $$
"""

# A contagem de alunos atravessa uma fronteira de proposito: o papel de runtime
# nao le learning_enrollment, e nao deve passar a ler. Esta funcao devolve
# apenas numeros por produto, nunca quem e o aluno, e num unico retorno para a
# listagem nao virar uma consulta por linha.
_STUDENT_COUNTS = r"""
create function app_private.product_student_counts(
  p_identity_user_id uuid, p_seller_organization_id uuid
) returns table(catalog_product_id uuid, students bigint)
language plpgsql security definer set search_path = pg_catalog, app_private, public as $$
begin
  if app_private.context_text('capability') <> 'catalog.draft.read'
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id') is distinct from
        p_seller_organization_id then
    raise exception 'product cover context is not authorized' using errcode = '28000';
  end if;

  return query
    select enrolled.catalog_product_id,
           count(distinct enrolled.buyer_identity_user_id)::bigint
    from public.learning_enrollment enrolled
    where enrolled.seller_organization_id = p_seller_organization_id
    group by enrolled.catalog_product_id;
end $$
"""

_INDEX = r"""
create index if not exists learning_enrollment_product_idx
  on public.learning_enrollment(seller_organization_id,catalog_product_id)
"""

_SIGNATURES = (
    "app_private.create_product_cover_upload_intent"
    "(uuid,uuid,uuid,text,bigint,text,text,text,uuid,text,uuid)",
    "app_private.read_product_cover_upload(uuid,uuid,uuid,uuid)",
    "app_private.close_product_cover_upload(uuid,uuid,uuid,uuid,text,text,uuid)",
    "app_private.set_product_cover"
    "(uuid,uuid,uuid,uuid,text,text,text,text,integer,integer,bigint,bigint,uuid,text,uuid)",
    "app_private.clear_product_cover(uuid,uuid,uuid,bigint,uuid,text,uuid)",
    "app_private.product_student_counts(uuid,uuid)",
)

_READY_TAIL = (
    "'0025_seller_membership_role','0026_asset_worker_claim_filename',"
    "'0027_product_course_draft_update','0028_offer_activation_guard',"
    "'0029_product_draft_asset_access','0030_product_pending_assets',"
    "'0031_public_free_access','0032_platform_superadmin_catalog',"
    "'0033_pdf_study_state','0034_publish_access_links',"
    "'0035_document_support_material','0036_lesson_material_links')"
)
_READY_TAIL_WITH_0037 = _READY_TAIL[:-1] + ",'0037_product_cover')"
_READY_ANCHOR = "and to_regclass('public.lesson_material_link') is not null"
_READY_ADDITION = (
    _READY_ANCHOR
    + "\n            and to_regclass('app_private.product_cover_upload') is not null"
    + "\n            and to_regprocedure(\n              "
    + "'app_private.product_student_counts(uuid,uuid)'\n            ) is not null"
)


def upgrade() -> None:
    statements = [
        _COLUMNS,
        _CHECK,
        _INDEX,
        _UPLOAD_TABLE,
        _UPLOAD_PENDING_UNIQUE,
        _UPLOAD_STATUS_INDEX,
        _CREATE_INTENT,
        _READ_UPLOAD,
        _CLOSE_UPLOAD,
        _SET_COVER,
        _CLEAR_COVER,
        _STUDENT_COUNTS,
    ]
    for signature in _SIGNATURES:
        statements.append(f"revoke all on function {signature} from public")
        statements.append(f"grant execute on function {signature} to appsell_runtime")
    statements.append(
        f"""
        do $migration$
        declare definition text; changed text;
        begin
          definition:=pg_get_functiondef('app_private.schema_ready_base_0016()'::regprocedure);
          changed:=replace(definition,$old${_READY_TAIL}$old$,$new${_READY_TAIL_WITH_0037}$new$);
          if changed=definition then
            raise exception 'cannot extend 0016 readiness for 0037' using errcode='55000';
          end if;
          execute changed;
          definition:=pg_get_functiondef('app_private.schema_ready_base_0017()'::regprocedure);
          changed:=replace(definition,$old${_READY_TAIL}$old$,$new${_READY_TAIL_WITH_0037}$new$);
          if changed=definition then
            raise exception 'cannot extend 0017 readiness for 0037' using errcode='55000';
          end if;
          execute changed;
          definition:=pg_get_functiondef('app_private.schema_ready()'::regprocedure);
          changed:=replace(definition,$old${_READY_ANCHOR}$old$,$new${_READY_ADDITION}$new$);
          if changed=definition then
            raise exception 'cannot extend schema readiness for 0037' using errcode='55000';
          end if;
          execute changed;
        end $migration$
        """
    )
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    statements = [
        """
        do $migration$
        begin
          if exists (
            select 1 from public.catalog_product where cover_object_key is not null
          ) then
            raise exception 'cannot drop covers that exist' using errcode='55000';
          end if;
        end $migration$
        """,
        f"""
        do $migration$
        declare definition text; changed text;
        begin
          definition:=pg_get_functiondef('app_private.schema_ready()'::regprocedure);
          changed:=replace(definition,$old${_READY_ADDITION}$old$,$new${_READY_ANCHOR}$new$);
          if changed=definition then
            raise exception 'cannot restore readiness from 0037' using errcode='55000';
          end if;
          execute changed;
          definition:=pg_get_functiondef('app_private.schema_ready_base_0017()'::regprocedure);
          changed:=replace(definition,$old${_READY_TAIL_WITH_0037}$old$,$new${_READY_TAIL}$new$);
          if changed=definition then
            raise exception 'cannot restore 0017 readiness from 0037' using errcode='55000';
          end if;
          execute changed;
          definition:=pg_get_functiondef('app_private.schema_ready_base_0016()'::regprocedure);
          changed:=replace(definition,$old${_READY_TAIL_WITH_0037}$old$,$new${_READY_TAIL}$new$);
          if changed=definition then
            raise exception 'cannot restore 0016 readiness from 0037' using errcode='55000';
          end if;
          execute changed;
        end $migration$
        """,
    ]
    for signature in _SIGNATURES:
        statements.append(f"drop function {signature}")
    statements.append("drop table app_private.product_cover_upload")
    statements.append("drop index if exists learning_enrollment_product_idx")
    statements.append(
        "alter table public.catalog_product drop constraint catalog_product_cover_complete"
    )
    statements.append(
        """
        alter table public.catalog_product
          drop column cover_object_key,
          drop column cover_storage_version_id,
          drop column cover_media_type,
          drop column cover_sha256,
          drop column cover_width,
          drop column cover_height,
          drop column cover_byte_size,
          drop column cover_updated_at
        """
    )
    for statement in statements:
        op.execute(statement)
