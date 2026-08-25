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

The digest stored is the digest of the **sanitised** file. The API rewrites
every cover to strip metadata, so recording what the seller uploaded would make
this table describe something that is not what is stored.
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
  add column cover_media_type text,
  add column cover_sha256 text,
  add column cover_width integer,
  add column cover_height integer,
  add column cover_byte_size bigint,
  add column cover_updated_at timestamptz
"""

# Uma capa existe inteira ou não existe. Nada de linha meio escrita.
_CHECK = r"""
alter table public.catalog_product
  add constraint catalog_product_cover_complete check (
    (cover_object_key is null and cover_media_type is null and cover_sha256 is null
     and cover_width is null and cover_height is null and cover_byte_size is null
     and cover_updated_at is null)
    or
    (cover_object_key is not null and cover_media_type is not null
     and cover_sha256 is not null and cover_width is not null
     and cover_height is not null and cover_byte_size is not null
     and cover_updated_at is not null
     and cover_media_type in ('image/jpeg','image/png','image/webp')
     and cover_sha256 ~ '^[0-9a-f]{64}$'
     and cover_width between 64 and 4000
     and cover_height between 64 and 4000
     and cover_byte_size between 1 and 5242880
     and length(cover_object_key) between 1 and 400)
  )
"""

# A autorização é a mesma das outras funções de catálogo: o contexto da
# transação, posto por apply_request_context e verificado aqui. Escrito por
# extenso em cada função em vez de montado por concatenação, como no resto das
# migrations — SQL neste repositório é literal, e literal é o que se lê.
_SET_COVER = r"""
create function app_private.set_product_cover(
  p_identity_user_id uuid, p_seller_organization_id uuid, p_catalog_product_id uuid,
  p_object_key text, p_media_type text, p_sha256 text,
  p_width integer, p_height integer, p_byte_size bigint,
  p_expected_product_version bigint, p_idempotency_key uuid,
  p_body_sha256 text, p_request_id uuid
) returns table(product_version bigint)
language plpgsql security definer set search_path = pg_catalog, app_private, public as $$
declare locked_product public.catalog_product%rowtype;
begin
  if app_private.context_text('capability') <> 'catalog.draft.update'
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id') is distinct from
        p_seller_organization_id then
    raise exception 'product cover context is not authorized' using errcode = '28000';
  end if;

  if p_object_key is null or octet_length(p_object_key) not between 1 and 400
     or p_media_type is null
     or p_media_type not in ('image/jpeg','image/png','image/webp')
     or p_sha256 is null or p_sha256 !~ '^[0-9a-f]{64}$'
     or p_width is null or p_width not between 64 and 4000
     or p_height is null or p_height not between 64 and 4000
     or p_byte_size is null or p_byte_size not between 1 and 5242880
     or p_expected_product_version is null or p_expected_product_version < 1
     or p_idempotency_key is null or p_request_id is null
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
  if locked_product.status = 'archived' then
    raise exception 'archived product cannot take a cover' using errcode = 'P4224';
  end if;
  if locked_product.version <> p_expected_product_version then
    raise exception 'catalog product version conflict' using errcode = '40001';
  end if;

  update public.catalog_product product
  set cover_object_key = p_object_key,
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
  return next;
end $$
"""

_CLEAR_COVER = r"""
create function app_private.clear_product_cover(
  p_identity_user_id uuid, p_seller_organization_id uuid, p_catalog_product_id uuid,
  p_expected_product_version bigint, p_idempotency_key uuid,
  p_body_sha256 text, p_request_id uuid
) returns table(product_version bigint, changed boolean)
language plpgsql security definer set search_path = pg_catalog, app_private, public as $$
declare locked_product public.catalog_product%rowtype;
begin
  if app_private.context_text('capability') <> 'catalog.draft.update'
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id') is distinct from
        p_seller_organization_id then
    raise exception 'product cover context is not authorized' using errcode = '28000';
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
    return next;
    return;
  end if;
  if locked_product.version <> p_expected_product_version then
    raise exception 'catalog product version conflict' using errcode = '40001';
  end if;

  update public.catalog_product product
  set cover_object_key = null, cover_media_type = null, cover_sha256 = null,
      cover_width = null, cover_height = null, cover_byte_size = null,
      cover_updated_at = null,
      version = product.version + 1, updated_at = now()
  where product.seller_organization_id = p_seller_organization_id
    and product.id = p_catalog_product_id
  returning product.version into product_version;
  changed := true;
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
    "app_private.set_product_cover"
    "(uuid,uuid,uuid,text,text,text,integer,integer,bigint,bigint,uuid,text,uuid)",
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
    + "\n            and to_regprocedure(\n              "
    + "'app_private.product_student_counts(uuid,uuid)'\n            ) is not null"
)


def upgrade() -> None:
    statements = [_COLUMNS, _CHECK, _INDEX, _SET_COVER, _CLEAR_COVER, _STUDENT_COUNTS]
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
    statements.append("drop index if exists learning_enrollment_product_idx")
    statements.append(
        "alter table public.catalog_product drop constraint catalog_product_cover_complete"
    )
    statements.append(
        """
        alter table public.catalog_product
          drop column cover_object_key,
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
