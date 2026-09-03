"""Banner do produto, e o upload de imagem do produto ganha um tipo (#387).

A capa do produto (0037) é uma imagem só, usada em três lugares com formatos
diferentes: o cartão da loja, a lista do painel e o alto da página de vendas.
Nenhum arquivo sobrevive a essa variedade sem ser cortado, e o corte apareceu
para o vendedor no primeiro produto real.

Esta migration separa os papéis. A capa continua sendo a imagem quadrada-larga
que representa o produto (16:9); o banner é a faixa que abre a página de
vendas (16:5). Cada uma é pedida ao vendedor na sua medida, e nenhuma é
espremida na outra.

O desenho é o mesmo da 0054, que fez isso para o logo e o banner da loja: uma
coluna `kind` no registro de upload, e as cinco funções da capa passam a
receber o tipo. Quem já tinha capa continua com ela: as colunas antigas não são
tocadas.

Revision ID: 0056_banner_do_produto
Revises: 0055_seller_financial_binding
"""

from __future__ import annotations

from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0056_banner_do_produto"
down_revision = "0055_seller_financial_binding"
branch_labels = None
depends_on = None


def _modulo(nome: str, apelido: str) -> ModuleType:
    """Uma migration anterior como módulo: o texto de origem das funções."""
    caminho = Path(__file__).with_name(nome)
    spec = importlib_util.spec_from_file_location(apelido, caminho)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"não consegui carregar {nome}")
    modulo = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


# ---------------------------------------------------------------- colunas

_COLUNAS_DO_BANNER = r"""
alter table public.catalog_product
  add column banner_object_key text,
  add column banner_storage_version_id text,
  add column banner_media_type text,
  add column banner_sha256 text,
  add column banner_width integer,
  add column banner_height integer,
  add column banner_byte_size bigint,
  add column banner_updated_at timestamptz
"""

# Um banner existe inteiro ou não existe, como a capa da 0037.
_CHECK_DO_BANNER = r"""
alter table public.catalog_product
  add constraint catalog_product_banner_complete check (
    (banner_object_key is null and banner_storage_version_id is null
     and banner_media_type is null and banner_sha256 is null
     and banner_width is null and banner_height is null and banner_byte_size is null
     and banner_updated_at is null)
    or
    (banner_object_key is not null and banner_storage_version_id is not null
     and banner_media_type is not null
     and banner_sha256 is not null and banner_width is not null
     and banner_height is not null and banner_byte_size is not null
     and banner_updated_at is not null
     and banner_media_type in ('image/jpeg','image/png','image/webp')
     and banner_sha256 ~ '^[0-9a-f]{64}$'
     and banner_width between 64 and 4000
     and banner_height between 64 and 4000
     and banner_byte_size between 1 and 5242880
     and length(banner_object_key) between 1 and 400
     and length(banner_storage_version_id) between 1 and 512)
  )
"""

# O registro de upload passa a dizer de qual imagem ele é. O padrão 'cover'
# mantém verdadeiras as linhas que já existem.
_COLUNA_KIND = r"""
alter table app_private.product_cover_upload
  add column kind text not null default 'cover'
    constraint product_cover_upload_kind check (kind in ('cover','banner'))
"""

# Um pendente por produto e por tipo: enviar a capa não cancela o banner.
_PENDENTE_POR_TIPO = r"""
create unique index product_cover_upload_one_pending_per_kind_idx
  on app_private.product_cover_upload(seller_organization_id,catalog_product_id,kind)
  where status = 'pending'
"""

# A guarda é a mesma das outras funções de catálogo, escrita por extenso em
# cada uma. Aqui ela entra por substituição para não divergir entre as cinco.
_GUARDA = """
  if app_private.context_text('capability') <> 'catalog.draft.update'
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id') is distinct from
        p_seller_organization_id then
    raise exception 'product image context is not authorized' using errcode = '28000';
  end if;
  if p_kind is null or p_kind not in ('cover','banner') then
    raise exception 'invalid product image kind' using errcode = '22023';
  end if;
"""

_CRIAR_INTENT = r"""
create function app_private.create_product_image_upload_intent(
  p_identity_user_id uuid, p_seller_organization_id uuid, p_catalog_product_id uuid,
  p_kind text, p_media_type text, p_byte_size bigint, p_sha256 text,
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
GUARDA
  if p_media_type is null
     or p_media_type not in ('image/jpeg','image/png','image/webp')
     or p_byte_size is null or p_byte_size not between 1 and 5242880
     or p_sha256 is null or p_sha256 !~ '^[0-9a-f]{64}$'
     or p_quarantine_bucket is null or p_ready_bucket is null
     or p_quarantine_bucket = p_ready_bucket
     or p_idempotency_key is null or p_request_id is null
     or p_body_sha256 is null or p_body_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception 'invalid product image upload intent' using errcode = '22023';
  end if;

  select * into locked_product from public.catalog_product product
  where product.seller_organization_id = p_seller_organization_id
    and product.id = p_catalog_product_id
  for update;
  if not found then
    raise exception 'catalog product not found' using errcode = 'P4041';
  end if;
  if locked_product.status = 'archived' then
    raise exception 'archived product cannot take an image' using errcode = 'P4228';
  end if;

  -- Uma tentativa anterior do mesmo produto e do mesmo tipo é substituída, não
  -- enfileirada: quem fechou o seletor de arquivo não fica travado até expirar.
  update app_private.product_cover_upload attempt
  set status = case when attempt.expires_at <= clock_timestamp() then 'expired' else 'cancelled' end,
      failure_code = 'superseded',
      updated_at = clock_timestamp()
  where attempt.seller_organization_id = p_seller_organization_id
    and attempt.catalog_product_id = p_catalog_product_id
    and attempt.kind = p_kind
    and attempt.status = 'pending';

  quarantine := 'tenants/' || p_seller_organization_id::text
    || '/quarantine/' || p_kind || '/' || p_catalog_product_id::text;
  ready := 'tenants/' || p_seller_organization_id::text
    || case when p_kind = 'cover' then '/covers/' else '/banners/' end
    || p_catalog_product_id::text || '/' || upload_id::text;
  expires := clock_timestamp() + interval '15 minutes';

  insert into app_private.product_cover_upload(
    seller_organization_id,id,identity_user_id,catalog_product_id,kind,
    expected_media_type,expected_byte_size,expected_sha256,
    quarantine_bucket,ready_bucket,quarantine_key,ready_key,status,expires_at
  ) values(
    p_seller_organization_id,upload_id,p_identity_user_id,p_catalog_product_id,p_kind,
    p_media_type,p_byte_size,p_sha256,
    p_quarantine_bucket,p_ready_bucket,quarantine,ready,'pending',expires
  );

  insert into public.audit_event(
    seller_organization_id,actor_identity_user_id,action,target_type,target_id,
    result,request_id,metadata
  ) values(
    p_seller_organization_id,p_identity_user_id,
    'catalog.product_' || p_kind || '_upload_created',
    'catalog_product',p_catalog_product_id,'succeeded',p_request_id,
    jsonb_build_object(
      'upload_id',upload_id,'kind',p_kind,'media_type',p_media_type,
      'byte_size',p_byte_size,'idempotency_key',p_idempotency_key
    )
  );

  return jsonb_build_object(
    'upload_id',upload_id,'kind',p_kind,'quarantine_bucket',p_quarantine_bucket,
    'quarantine_key',quarantine,'ready_key',ready,
    'media_type',p_media_type,'byte_size',p_byte_size,'sha256',p_sha256,
    'expires_at',expires,'product_version',locked_product.version
  );
end $$
"""

_LER_UPLOAD = r"""
create function app_private.read_product_image_upload(
  p_identity_user_id uuid, p_seller_organization_id uuid, p_catalog_product_id uuid,
  p_kind text, p_upload_id uuid
) returns jsonb
language plpgsql security definer set search_path = pg_catalog, app_private, public as $$
declare found_upload app_private.product_cover_upload%rowtype;
begin
GUARDA
  -- O tipo faz parte da identidade do upload: um id de capa não é lido pela
  -- rota do banner, nem o contrário.
  select * into found_upload from app_private.product_cover_upload attempt
  where attempt.seller_organization_id = p_seller_organization_id
    and attempt.catalog_product_id = p_catalog_product_id
    and attempt.kind = p_kind
    and attempt.id = p_upload_id;
  if not found then
    raise exception 'product image upload not found' using errcode = 'P4041';
  end if;
  return jsonb_build_object(
    'upload_id',found_upload.id,'kind',found_upload.kind,'status',found_upload.status,
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

_FECHAR_UPLOAD = r"""
create function app_private.close_product_image_upload(
  p_identity_user_id uuid, p_seller_organization_id uuid, p_catalog_product_id uuid,
  p_kind text, p_upload_id uuid, p_status text, p_failure_code text, p_request_id uuid
) returns jsonb
language plpgsql security definer set search_path = pg_catalog, app_private, public as $$
declare closed app_private.product_cover_upload%rowtype;
begin
GUARDA
  if p_status is null or p_status not in ('rejected','cancelled','expired')
     or p_request_id is null
     or (p_failure_code is not null
         and octet_length(p_failure_code) not between 2 and 80) then
    raise exception 'invalid product image upload closure' using errcode = '22023';
  end if;

  select * into closed from app_private.product_cover_upload attempt
  where attempt.seller_organization_id = p_seller_organization_id
    and attempt.catalog_product_id = p_catalog_product_id
    and attempt.kind = p_kind
    and attempt.id = p_upload_id
  for update;
  if not found then
    raise exception 'product image upload not found' using errcode = 'P4041';
  end if;
  -- Fechar duas vezes chega ao mesmo estado que fechar uma.
  if closed.status <> 'pending' then
    return jsonb_build_object('upload_id',closed.id,'status',closed.status,'changed',false);
  end if;

  update app_private.product_cover_upload attempt
  set status = p_status, failure_code = p_failure_code, updated_at = clock_timestamp()
  where attempt.seller_organization_id = p_seller_organization_id
    and attempt.kind = p_kind
    and attempt.id = p_upload_id;

  insert into public.audit_event(
    seller_organization_id,actor_identity_user_id,action,target_type,target_id,
    result,request_id,metadata
  ) values(
    p_seller_organization_id,p_identity_user_id,
    'catalog.product_' || p_kind || '_upload_closed',
    'catalog_product',p_catalog_product_id,
    case when p_status = 'rejected' then 'failed' else 'succeeded' end,
    p_request_id,
    jsonb_build_object('upload_id',p_upload_id,'kind',p_kind,'status',p_status,
                       'failure_code',p_failure_code)
  );

  return jsonb_build_object('upload_id',p_upload_id,'status',p_status,'changed',true);
end $$
"""

_GRAVAR_IMAGEM = r"""
create function app_private.set_product_image(
  p_identity_user_id uuid, p_seller_organization_id uuid, p_catalog_product_id uuid,
  p_kind text, p_upload_id uuid, p_object_key text, p_storage_version_id text,
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
GUARDA
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
    raise exception 'invalid product image request' using errcode = '22023';
  end if;

  select * into locked_upload from app_private.product_cover_upload attempt
  where attempt.seller_organization_id = p_seller_organization_id
    and attempt.catalog_product_id = p_catalog_product_id
    and attempt.kind = p_kind
    and attempt.id = p_upload_id
  for update;
  if not found then
    raise exception 'product image upload not found' using errcode = 'P4041';
  end if;
  if locked_upload.status <> 'pending' then
    raise exception 'product image upload is closed' using errcode = 'P4229';
  end if;
  -- A chave é a do intent, nunca a que o chamador quis. Sem isto, quem falasse
  -- com esta função poderia apontar a imagem para qualquer objeto do bucket.
  if locked_upload.ready_key <> p_object_key then
    raise exception 'product image key is not the reserved one' using errcode = '28000';
  end if;
  if locked_upload.expected_media_type <> p_media_type then
    raise exception 'product image media type differs from the upload intent'
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
    raise exception 'archived product cannot take an image' using errcode = 'P4228';
  end if;
  if locked_product.version <> p_expected_product_version then
    raise exception 'catalog product version conflict' using errcode = '40001';
  end if;

  -- Duas colunas diferentes, dois updates escritos por extenso: montar o nome
  -- da coluna em texto tornaria esta função dinâmica, e ela é definer.
  if p_kind = 'cover' then
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
  else
    previous_object_key := locked_product.banner_object_key;
    update public.catalog_product product
    set banner_object_key = p_object_key,
        banner_storage_version_id = p_storage_version_id,
        banner_media_type = p_media_type,
        banner_sha256 = p_sha256,
        banner_width = p_width,
        banner_height = p_height,
        banner_byte_size = p_byte_size,
        banner_updated_at = now(),
        version = product.version + 1,
        updated_at = now()
    where product.seller_organization_id = p_seller_organization_id
      and product.id = p_catalog_product_id
    returning product.version into product_version;
  end if;

  update app_private.product_cover_upload attempt
  set status = 'ready', updated_at = clock_timestamp()
  where attempt.seller_organization_id = p_seller_organization_id
    and attempt.id = p_upload_id;

  insert into public.audit_event(
    seller_organization_id,actor_identity_user_id,action,target_type,target_id,
    result,request_id,metadata
  ) values(
    p_seller_organization_id,p_identity_user_id,
    'catalog.product_' || p_kind || '_set',
    'catalog_product',p_catalog_product_id,'succeeded',p_request_id,
    jsonb_build_object(
      'upload_id',p_upload_id,'kind',p_kind,'media_type',p_media_type,
      'byte_size',p_byte_size,'width',p_width,'height',p_height,
      'idempotency_key',p_idempotency_key
    )
  );
  return next;
end $$
"""

_APAGAR_IMAGEM = r"""
create function app_private.clear_product_image(
  p_identity_user_id uuid, p_seller_organization_id uuid, p_catalog_product_id uuid,
  p_kind text, p_expected_product_version bigint, p_idempotency_key uuid,
  p_body_sha256 text, p_request_id uuid
) returns table(product_version bigint, changed boolean, previous_object_key text)
language plpgsql security definer set search_path = pg_catalog, app_private, public as $$
declare
  locked_product public.catalog_product%rowtype;
  atual text;
begin
GUARDA
  if p_idempotency_key is null or p_request_id is null
     or p_body_sha256 is null or p_body_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception 'invalid product image request' using errcode = '22023';
  end if;

  select * into locked_product from public.catalog_product product
  where product.seller_organization_id = p_seller_organization_id
    and product.id = p_catalog_product_id
  for update;
  if not found then
    raise exception 'catalog product not found' using errcode = 'P4041';
  end if;

  atual := case when p_kind = 'cover'
    then locked_product.cover_object_key else locked_product.banner_object_key end;
  -- Remover uma imagem que já não existe é sucesso, não erro.
  if atual is null then
    product_version := locked_product.version;
    changed := false;
    previous_object_key := null;
    return next;
    return;
  end if;
  if locked_product.version <> p_expected_product_version then
    raise exception 'catalog product version conflict' using errcode = '40001';
  end if;

  previous_object_key := atual;

  if p_kind = 'cover' then
    update public.catalog_product product
    set cover_object_key = null, cover_storage_version_id = null,
        cover_media_type = null, cover_sha256 = null,
        cover_width = null, cover_height = null, cover_byte_size = null,
        cover_updated_at = null,
        version = product.version + 1, updated_at = now()
    where product.seller_organization_id = p_seller_organization_id
      and product.id = p_catalog_product_id
    returning product.version into product_version;
  else
    update public.catalog_product product
    set banner_object_key = null, banner_storage_version_id = null,
        banner_media_type = null, banner_sha256 = null,
        banner_width = null, banner_height = null, banner_byte_size = null,
        banner_updated_at = null,
        version = product.version + 1, updated_at = now()
    where product.seller_organization_id = p_seller_organization_id
      and product.id = p_catalog_product_id
    returning product.version into product_version;
  end if;
  changed := true;

  insert into public.audit_event(
    seller_organization_id,actor_identity_user_id,action,target_type,target_id,
    result,request_id,metadata
  ) values(
    p_seller_organization_id,p_identity_user_id,
    'catalog.product_' || p_kind || '_cleared',
    'catalog_product',p_catalog_product_id,'succeeded',p_request_id,
    jsonb_build_object('kind',p_kind,'idempotency_key',p_idempotency_key)
  );
  return next;
end $$
"""

_ATALHOS_DA_CAPA = r"""
create function app_private.create_product_cover_upload_intent(
  p_identity_user_id uuid, p_seller_organization_id uuid, p_catalog_product_id uuid,
  p_media_type text, p_byte_size bigint, p_sha256 text,
  p_quarantine_bucket text, p_ready_bucket text,
  p_idempotency_key uuid, p_body_sha256 text, p_request_id uuid
) returns jsonb
language sql security definer set search_path = pg_catalog, app_private, public as $$
  select app_private.create_product_image_upload_intent(
    p_identity_user_id, p_seller_organization_id, p_catalog_product_id, 'cover',
    p_media_type, p_byte_size, p_sha256, p_quarantine_bucket, p_ready_bucket,
    p_idempotency_key, p_body_sha256, p_request_id)
$$;

create function app_private.read_product_cover_upload(
  p_identity_user_id uuid, p_seller_organization_id uuid, p_catalog_product_id uuid,
  p_upload_id uuid
) returns jsonb
language sql security definer set search_path = pg_catalog, app_private, public as $$
  select app_private.read_product_image_upload(
    p_identity_user_id, p_seller_organization_id, p_catalog_product_id, 'cover', p_upload_id)
$$;

create function app_private.close_product_cover_upload(
  p_identity_user_id uuid, p_seller_organization_id uuid, p_catalog_product_id uuid,
  p_upload_id uuid, p_status text, p_failure_code text, p_request_id uuid
) returns jsonb
language sql security definer set search_path = pg_catalog, app_private, public as $$
  select app_private.close_product_image_upload(
    p_identity_user_id, p_seller_organization_id, p_catalog_product_id, 'cover',
    p_upload_id, p_status, p_failure_code, p_request_id)
$$;

create function app_private.set_product_cover(
  p_identity_user_id uuid, p_seller_organization_id uuid, p_catalog_product_id uuid,
  p_upload_id uuid, p_object_key text, p_storage_version_id text,
  p_media_type text, p_sha256 text,
  p_width integer, p_height integer, p_byte_size bigint,
  p_expected_product_version bigint, p_idempotency_key uuid,
  p_body_sha256 text, p_request_id uuid
) returns table(product_version bigint, previous_object_key text)
language sql security definer set search_path = pg_catalog, app_private, public as $$
  select * from app_private.set_product_image(
    p_identity_user_id, p_seller_organization_id, p_catalog_product_id, 'cover',
    p_upload_id, p_object_key, p_storage_version_id, p_media_type, p_sha256,
    p_width, p_height, p_byte_size, p_expected_product_version,
    p_idempotency_key, p_body_sha256, p_request_id)
$$;

create function app_private.clear_product_cover(
  p_identity_user_id uuid, p_seller_organization_id uuid, p_catalog_product_id uuid,
  p_expected_product_version bigint, p_idempotency_key uuid,
  p_body_sha256 text, p_request_id uuid
) returns table(product_version bigint, changed boolean, previous_object_key text)
language sql security definer set search_path = pg_catalog, app_private, public as $$
  select * from app_private.clear_product_image(
    p_identity_user_id, p_seller_organization_id, p_catalog_product_id, 'cover',
    p_expected_product_version, p_idempotency_key, p_body_sha256, p_request_id)
$$;
"""

_FUNCOES_DA_CAPA: tuple[str, ...] = (
    "app_private.create_product_cover_upload_intent"
    "(uuid,uuid,uuid,text,bigint,text,text,text,uuid,text,uuid)",
    "app_private.read_product_cover_upload(uuid,uuid,uuid,uuid)",
    "app_private.close_product_cover_upload(uuid,uuid,uuid,uuid,text,text,uuid)",
    "app_private.set_product_cover"
    "(uuid,uuid,uuid,uuid,text,text,text,text,integer,integer,bigint,bigint,uuid,text,uuid)",
    "app_private.clear_product_cover(uuid,uuid,uuid,bigint,uuid,text,uuid)",
)
_FUNCOES_DA_IMAGEM: tuple[str, ...] = (
    "app_private.create_product_image_upload_intent"
    "(uuid,uuid,uuid,text,text,bigint,text,text,text,uuid,text,uuid)",
    "app_private.read_product_image_upload(uuid,uuid,uuid,text,uuid)",
    "app_private.close_product_image_upload(uuid,uuid,uuid,text,uuid,text,text,uuid)",
    "app_private.set_product_image"
    "(uuid,uuid,uuid,text,uuid,text,text,text,text,integer,integer,bigint,bigint,uuid,text,uuid)",
    "app_private.clear_product_image(uuid,uuid,uuid,text,bigint,uuid,text,uuid)",
)

# -------------------------------------------------------------- schema_ready

_READY = "app_private.schema_ready()"
_BASES_DO_READY: tuple[str, ...] = (
    "app_private.schema_ready_base_0016()",
    "app_private.schema_ready_base_0017()",
)
_READY_ATE_0055 = (
    "'0053_dono_das_funcoes_payout','0054_banner_da_loja','0055_seller_financial_binding'))"
)
_READY_ATE_0056 = (
    "'0053_dono_das_funcoes_payout','0054_banner_da_loja','0055_seller_financial_binding',"
    "'0056_banner_do_produto'))"
)
# A 0037 registrou a tabela de upload no schema_ready, não as funções da capa.
# Ancoramos nela para declarar também a função de imagem: assim um banco que
# ficou no meio do caminho não passa por pronto.
_ANCORA_DO_UPLOAD = "and to_regclass('app_private.product_cover_upload') is not null"
_ANCORA_COM_IMAGEM = _ANCORA_DO_UPLOAD + (
    "\n            and to_regprocedure("
    "'app_private.set_product_image"
    "(uuid,uuid,uuid,text,uuid,text,text,text,text,integer,integer,bigint,bigint,uuid,text,uuid)'"
    ") is not null"
)

_REESCREVE = """
do $migration$
declare
  definicao text;
  reescrita text;
begin
  definicao := pg_get_functiondef('ALVO'::regprocedure);
  if (length(definicao) - length(replace(definicao, $de$DE$de$, ''))) / length($de$DE$de$) <> 1 then
    raise exception 'ALVO não continha a âncora exatamente uma vez' using errcode='55000';
  end if;
  reescrita := replace(definicao, $de$DE$de$, $para$PARA$para$);
  execute reescrita;
end $migration$
"""


def _com_guarda(sql: str) -> str:
    return sql.replace("GUARDA", _GUARDA.strip("\n"))


def upgrade() -> None:
    # DDL de tabela como o migrador, a lição da 0053; as funções voltam a
    # nascer como appsell_owner logo abaixo.
    op.execute("reset role")
    op.execute(_COLUNAS_DO_BANNER)
    op.execute(_CHECK_DO_BANNER)
    op.execute(_COLUNA_KIND)
    op.execute("drop index app_private.product_cover_upload_one_pending_idx")
    op.execute(_PENDENTE_POR_TIPO)
    op.execute("alter table app_private.product_cover_upload owner to appsell_owner")
    op.execute("set local role appsell_owner")

    for assinatura in _FUNCOES_DA_CAPA:
        op.execute(f"revoke all on function {assinatura} from public")
        op.execute(f"drop function {assinatura}")
    for sql in (_CRIAR_INTENT, _LER_UPLOAD, _FECHAR_UPLOAD, _GRAVAR_IMAGEM, _APAGAR_IMAGEM):
        op.execute(_com_guarda(sql))
    # Os nomes antigos voltam como atalho para o tipo "cover": a API em
    # produção continua chamando o que sempre chamou até o deploy seguinte.
    # Eles saem numa migration futura, quando não houver mais chamador.
    op.execute(_ATALHOS_DA_CAPA)
    for assinatura in _FUNCOES_DA_IMAGEM + _FUNCOES_DA_CAPA:
        op.execute(f"alter function {assinatura} owner to appsell_owner")
        op.execute(f"revoke all on function {assinatura} from public")
        op.execute(f"grant execute on function {assinatura} to appsell_runtime")

    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0055)
            .replace("PARA", _READY_ATE_0056)
        )
    op.execute(
        _REESCREVE.replace("ALVO", _READY)
        .replace("DE", _ANCORA_DO_UPLOAD)
        .replace("PARA", _ANCORA_COM_IMAGEM)
    )


def downgrade() -> None:
    m37 = _modulo("0037_product_cover.py", "appsell_migration_0037")

    op.execute(
        _REESCREVE.replace("ALVO", _READY)
        .replace("DE", _ANCORA_COM_IMAGEM)
        .replace("PARA", _ANCORA_DO_UPLOAD)
    )
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0056)
            .replace("PARA", _READY_ATE_0055)
        )

    for assinatura in _FUNCOES_DA_CAPA:
        op.execute(f"revoke all on function {assinatura} from public")
        op.execute(f"drop function {assinatura}")
    for assinatura in _FUNCOES_DA_IMAGEM:
        op.execute(f"revoke all on function {assinatura} from public")
        op.execute(f"drop function {assinatura}")
    for sql in (
        m37._CREATE_INTENT,
        m37._READ_UPLOAD,
        m37._CLOSE_UPLOAD,
        m37._SET_COVER,
        m37._CLEAR_COVER,
    ):
        op.execute(sql)
    for assinatura in _FUNCOES_DA_CAPA:
        op.execute(f"alter function {assinatura} owner to appsell_owner")
        op.execute(f"revoke all on function {assinatura} from public")
        op.execute(f"grant execute on function {assinatura} to appsell_runtime")

    op.execute("reset role")
    # Sem isto, um produto com capa e banner pendentes ao mesmo tempo — estado
    # que o upgrade permite — impede o índice antigo de voltar, e o rollback
    # trava no meio.
    op.execute(
        "update app_private.product_cover_upload"
        " set status = 'cancelled', failure_code = 'downgraded',"
        " updated_at = clock_timestamp()"
        " where kind = 'banner' and status = 'pending'"
    )
    op.execute("drop index app_private.product_cover_upload_one_pending_per_kind_idx")
    op.execute(
        "create unique index product_cover_upload_one_pending_idx"
        " on app_private.product_cover_upload(seller_organization_id,catalog_product_id)"
        " where status = 'pending'"
    )
    op.execute("alter table app_private.product_cover_upload drop column kind")
    op.execute("alter table public.catalog_product drop constraint catalog_product_banner_complete")
    op.execute(
        "alter table public.catalog_product"
        " drop column banner_object_key, drop column banner_storage_version_id,"
        " drop column banner_media_type, drop column banner_sha256,"
        " drop column banner_width, drop column banner_height,"
        " drop column banner_byte_size, drop column banner_updated_at"
    )
    op.execute("set local role appsell_owner")
