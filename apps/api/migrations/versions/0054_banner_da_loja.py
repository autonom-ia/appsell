"""Banner da loja, e o upload de imagem da loja ganha um tipo (#387).

A capa larga no alto da vitrine — o padrão que dá seriedade a uma página de
marca: logo sobreposto, texto embaixo. Oito colunas `banner_*` com o mesmo
contrato tudo-ou-nada do logo e da capa do produto.

Em vez de uma segunda tabela de upload e cinco funções repetidas, a tabela
`seller_logo_upload` ganha `kind` (`logo` | `banner`) e as cinco funções do
logo viram cinco funções de imagem da loja com `p_kind`:
`create_seller_image_upload_intent`, `read_seller_image_upload`,
`close_seller_image_upload`, `set_seller_image`, `clear_seller_image`. As do
logo saem. Chaves por tipo: `tenants/<loja>/quarantine/<kind>` e
`tenants/<loja>/<kind>/<upload>`. No máximo um upload pendente por loja e
tipo.

A projeção das configurações e a vitrine pública ganham `banner`, condicional
como o logo. O downgrade reconstrói as funções e a vitrine exatamente com o
texto da 0052 (carregado do próprio arquivo dela), não com uma cópia.

Tamanho recomendado ao vendedor: 1600×500 (3,2:1), com o que importa no
centro; o verificador é o mesmo da capa (até 5 MiB, lados entre 64 e 4000).

Ordem de deploy: web → api → migration, como na 0052 e pelo mesmo motivo (o
parser do web antigo exige chaves exatas; `banner` só entra quando existe, mas
os nomes das funções mudam e a API nova precisa estar no ar antes).
"""

from __future__ import annotations

from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0054_banner_da_loja"
down_revision = "0053_dono_das_funcoes_payout"
branch_labels = None
depends_on = None

_READY = "app_private.schema_ready()"
_BASES_DO_READY: tuple[str, ...] = (
    "app_private.schema_ready_base_0016()",
    "app_private.schema_ready_base_0017()",
)
_READY_ATE_0053 = (
    "'0043_material_role_by_media','0044_pdf_de_trezentas_paginas','0045_envio_em_partes',"
    "'0046_aula_de_meio_giga','0047_cota_de_armazenamento','0048_nome_de_quem_entrou',"
    "'0049_caixa_de_saida_de_email','0050_recibo_do_link_de_turma',"
    "'0051_minha_conta_do_comprador','0052_configuracoes_da_loja',"
    "'0053_dono_das_funcoes_payout'))"
)
_READY_ATE_0054 = (
    "'0043_material_role_by_media','0044_pdf_de_trezentas_paginas','0045_envio_em_partes',"
    "'0046_aula_de_meio_giga','0047_cota_de_armazenamento','0048_nome_de_quem_entrou',"
    "'0049_caixa_de_saida_de_email','0050_recibo_do_link_de_turma',"
    "'0051_minha_conta_do_comprador','0052_configuracoes_da_loja',"
    "'0053_dono_das_funcoes_payout','0054_banner_da_loja'))"
)
# A linha que a 0052 pôs em schema_ready() para o logo; esta troca pela imagem.
_ANCORA_DO_LOGO = (
    "            and to_regprocedure("
    "'app_private.set_seller_logo(uuid,uuid,uuid,text,text,text,text,integer,integer,bigint,uuid,text,uuid)'"
    ") is not null\n"
)
_ANCORA_DA_IMAGEM = (
    "            and to_regprocedure("
    "'app_private.set_seller_image(uuid,uuid,text,uuid,text,text,text,text,integer,integer,bigint,uuid,text,uuid)'"
    ") is not null\n"
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
end $migration$;
"""

_FUNCOES_DO_LOGO: tuple[str, ...] = (
    "app_private.create_seller_logo_upload_intent(uuid,uuid,text,bigint,text,text,text,uuid,text,uuid)",
    "app_private.read_seller_logo_upload(uuid,uuid,uuid)",
    "app_private.close_seller_logo_upload(uuid,uuid,uuid,text,text,uuid)",
    "app_private.set_seller_logo(uuid,uuid,uuid,text,text,text,text,integer,integer,bigint,uuid,text,uuid)",
    "app_private.clear_seller_logo(uuid,uuid,uuid,text,uuid)",
)
_FUNCOES_DA_IMAGEM: tuple[str, ...] = (
    "app_private.create_seller_image_upload_intent(uuid,uuid,text,text,bigint,text,text,text,uuid,text,uuid)",
    "app_private.read_seller_image_upload(uuid,uuid,uuid)",
    "app_private.close_seller_image_upload(uuid,uuid,uuid,text,text,uuid)",
    "app_private.set_seller_image(uuid,uuid,text,uuid,text,text,text,text,integer,integer,bigint,uuid,text,uuid)",
    "app_private.clear_seller_image(uuid,uuid,text,uuid,text,uuid)",
)
_PROJECAO_SIGNATURE = "app_private.seller_settings_projection(public.seller_organization)"


def _modulo_0052() -> ModuleType:
    """A 0052 como módulo: o texto de origem das funções, para o downgrade."""
    caminho = Path(__file__).with_name("0052_configuracoes_da_loja.py")
    spec = importlib_util.spec_from_file_location("appsell_migration_0052", caminho)
    if spec is None or spec.loader is None:
        raise RuntimeError("não consegui carregar a 0052 para o downgrade")
    modulo = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


# ---------------------------------------------------------------- colunas

_COLUNAS_DO_BANNER = r"""
alter table public.seller_organization
  add column banner_object_key text,
  add column banner_storage_version_id text,
  add column banner_media_type text,
  add column banner_sha256 text,
  add column banner_width integer,
  add column banner_height integer,
  add column banner_byte_size bigint,
  add column banner_updated_at timestamptz
"""

_CHECK_DO_BANNER = r"""
alter table public.seller_organization
  add constraint seller_organization_banner_complete check (
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

_COLUNA_KIND = r"""
alter table app_private.seller_logo_upload
  add column kind text not null default 'logo'
    constraint seller_logo_upload_kind check (kind in ('logo','banner'))
"""

_PENDENTE_POR_TIPO = r"""
create unique index seller_logo_upload_one_pending_per_kind_idx
  on app_private.seller_logo_upload(seller_organization_id,kind)
  where status = 'pending'
"""

# ---------------------------------------------------------------- funções

_GUARDA = """
  if p_identity_user_id is null or p_seller_organization_id is null
     or app_private.context_text('capability') is distinct from 'seller.settings.update'
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id') is distinct from p_seller_organization_id
     or coalesce(app_private.resolve_seller_membership_role(p_seller_organization_id), '')
        not in ('seller_owner','seller_admin') then
    raise exception 'seller image context is not authorized' using errcode='42501';
  end if;
"""

_CRIAR_INTENT = r"""
create function app_private.create_seller_image_upload_intent(
  p_identity_user_id uuid, p_seller_organization_id uuid, p_kind text,
  p_media_type text, p_byte_size bigint, p_sha256 text,
  p_quarantine_bucket text, p_ready_bucket text,
  p_idempotency_key uuid, p_body_sha256 text, p_request_id uuid
) returns jsonb
language plpgsql security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
declare
  loja public.seller_organization%rowtype;
  upload_id uuid := gen_random_uuid();
  quarantine text;
  ready text;
  expires timestamptz;
begin
GUARDA
  if p_kind is null or p_kind not in ('logo','banner')
     or p_media_type is null
     or p_media_type not in ('image/jpeg','image/png','image/webp')
     or p_byte_size is null or p_byte_size not between 1 and 5242880
     or p_sha256 is null or p_sha256 !~ '^[0-9a-f]{64}$'
     or p_quarantine_bucket is null or p_ready_bucket is null
     or p_quarantine_bucket = p_ready_bucket
     or p_idempotency_key is null or p_request_id is null
     or p_body_sha256 is null or p_body_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception 'invalid seller image upload intent' using errcode='22023';
  end if;

  select * into loja from public.seller_organization
  where id=p_seller_organization_id for update;
  if not found then
    raise exception 'seller organization not found' using errcode='P4041';
  end if;
  if loja.status <> 'active' then
    raise exception 'seller organization is not active' using errcode='55000';
  end if;

  update app_private.seller_logo_upload attempt
  set status = case when attempt.expires_at <= clock_timestamp() then 'expired' else 'cancelled' end,
      failure_code = 'superseded',
      updated_at = clock_timestamp()
  where attempt.seller_organization_id = p_seller_organization_id
    and attempt.kind = p_kind
    and attempt.status = 'pending';

  quarantine := 'tenants/' || p_seller_organization_id::text || '/quarantine/' || p_kind;
  ready := 'tenants/' || p_seller_organization_id::text || '/' || p_kind || '/' || upload_id::text;
  expires := clock_timestamp() + interval '15 minutes';

  insert into app_private.seller_logo_upload(
    seller_organization_id,id,identity_user_id,kind,
    expected_media_type,expected_byte_size,expected_sha256,
    quarantine_bucket,ready_bucket,quarantine_key,ready_key,status,expires_at
  ) values(
    p_seller_organization_id,upload_id,p_identity_user_id,p_kind,
    p_media_type,p_byte_size,p_sha256,
    p_quarantine_bucket,p_ready_bucket,quarantine,ready,'pending',expires
  );

  insert into public.audit_event(
    seller_organization_id,actor_identity_user_id,action,target_type,target_id,
    result,request_id,metadata
  ) values(
    p_seller_organization_id,p_identity_user_id,'seller.' || p_kind || '_upload_created',
    'seller_organization',p_seller_organization_id,'succeeded',p_request_id,
    jsonb_build_object(
      'upload_id',upload_id,'kind',p_kind,'media_type',p_media_type,'byte_size',p_byte_size,
      'idempotency_key',p_idempotency_key
    )
  );

  return jsonb_build_object(
    'upload_id',upload_id,'kind',p_kind,'quarantine_bucket',p_quarantine_bucket,
    'quarantine_key',quarantine,'ready_key',ready,
    'media_type',p_media_type,'byte_size',p_byte_size,'sha256',p_sha256,
    'expires_at',expires
  );
end $$
"""

_LER_UPLOAD = r"""
create function app_private.read_seller_image_upload(
  p_identity_user_id uuid, p_seller_organization_id uuid, p_upload_id uuid
) returns jsonb
language plpgsql security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
declare found_upload app_private.seller_logo_upload%rowtype;
begin
GUARDA
  select * into found_upload from app_private.seller_logo_upload attempt
  where attempt.seller_organization_id = p_seller_organization_id
    and attempt.id = p_upload_id;
  if not found then
    raise exception 'seller image upload not found' using errcode='P4041';
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
create function app_private.close_seller_image_upload(
  p_identity_user_id uuid, p_seller_organization_id uuid,
  p_upload_id uuid, p_status text, p_failure_code text, p_request_id uuid
) returns jsonb
language plpgsql security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
declare closed app_private.seller_logo_upload%rowtype;
begin
GUARDA
  if p_status is null or p_status not in ('rejected','cancelled','expired')
     or p_request_id is null
     or (p_failure_code is not null
         and octet_length(p_failure_code) not between 2 and 80) then
    raise exception 'invalid seller image upload closure' using errcode='22023';
  end if;

  select * into closed from app_private.seller_logo_upload attempt
  where attempt.seller_organization_id = p_seller_organization_id
    and attempt.id = p_upload_id
  for update;
  if not found then
    raise exception 'seller image upload not found' using errcode='P4041';
  end if;
  if closed.status <> 'pending' then
    return jsonb_build_object('upload_id',closed.id,'status',closed.status,'changed',false);
  end if;

  update app_private.seller_logo_upload attempt
  set status = p_status, failure_code = p_failure_code, updated_at = clock_timestamp()
  where attempt.seller_organization_id = p_seller_organization_id
    and attempt.id = p_upload_id;

  insert into public.audit_event(
    seller_organization_id,actor_identity_user_id,action,target_type,target_id,
    result,request_id,metadata
  ) values(
    p_seller_organization_id,p_identity_user_id,'seller.' || closed.kind || '_upload_closed',
    'seller_organization',p_seller_organization_id,
    case when p_status = 'rejected' then 'failed' else 'succeeded' end,
    p_request_id,
    jsonb_build_object('upload_id',p_upload_id,'kind',closed.kind,'status',p_status,
                       'failure_code',p_failure_code)
  );
  return jsonb_build_object('upload_id',p_upload_id,'status',p_status,'changed',true);
end $$
"""

_GRAVAR_IMAGEM = r"""
create function app_private.set_seller_image(
  p_identity_user_id uuid, p_seller_organization_id uuid, p_kind text,
  p_upload_id uuid, p_object_key text, p_storage_version_id text,
  p_media_type text, p_sha256 text,
  p_width integer, p_height integer, p_byte_size bigint,
  p_idempotency_key uuid, p_body_sha256 text, p_request_id uuid
) returns table(previous_object_key text)
language plpgsql security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
declare
  loja public.seller_organization%rowtype;
  locked_upload app_private.seller_logo_upload%rowtype;
begin
GUARDA
  if p_kind is null or p_kind not in ('logo','banner')
     or p_object_key is null or octet_length(p_object_key) not between 1 and 400
     or p_storage_version_id is null
     or octet_length(p_storage_version_id) not between 1 and 512
     or p_media_type is null
     or p_media_type not in ('image/jpeg','image/png','image/webp')
     or p_sha256 is null or p_sha256 !~ '^[0-9a-f]{64}$'
     or p_width is null or p_width not between 64 and 4000
     or p_height is null or p_height not between 64 and 4000
     or p_byte_size is null or p_byte_size not between 1 and 5242880
     or p_upload_id is null
     or p_idempotency_key is null or p_request_id is null
     or p_body_sha256 is null or p_body_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception 'invalid seller image request' using errcode='22023';
  end if;

  select * into locked_upload from app_private.seller_logo_upload attempt
  where attempt.seller_organization_id = p_seller_organization_id
    and attempt.id = p_upload_id
  for update;
  if not found then
    raise exception 'seller image upload not found' using errcode='P4041';
  end if;
  if locked_upload.status <> 'pending' then
    raise exception 'seller image upload is closed' using errcode='P4229';
  end if;
  if locked_upload.kind <> p_kind then
    raise exception 'seller image kind differs from the upload intent' using errcode='22023';
  end if;
  if locked_upload.ready_key <> p_object_key then
    raise exception 'seller image key is not the reserved one' using errcode='28000';
  end if;
  if locked_upload.expected_media_type <> p_media_type then
    raise exception 'seller image media type differs from the upload intent'
      using errcode='22023';
  end if;

  select * into loja from public.seller_organization
  where id=p_seller_organization_id for update;
  if not found then
    raise exception 'seller organization not found' using errcode='P4041';
  end if;
  if loja.status <> 'active' then
    raise exception 'seller organization is not active' using errcode='55000';
  end if;

  if p_kind = 'logo' then
    previous_object_key := loja.logo_object_key;
    update public.seller_organization
    set logo_object_key = p_object_key, logo_storage_version_id = p_storage_version_id,
        logo_media_type = p_media_type, logo_sha256 = p_sha256,
        logo_width = p_width, logo_height = p_height, logo_byte_size = p_byte_size,
        logo_updated_at = clock_timestamp(), updated_at = clock_timestamp()
    where id = p_seller_organization_id;
  else
    previous_object_key := loja.banner_object_key;
    update public.seller_organization
    set banner_object_key = p_object_key, banner_storage_version_id = p_storage_version_id,
        banner_media_type = p_media_type, banner_sha256 = p_sha256,
        banner_width = p_width, banner_height = p_height, banner_byte_size = p_byte_size,
        banner_updated_at = clock_timestamp(), updated_at = clock_timestamp()
    where id = p_seller_organization_id;
  end if;

  update app_private.seller_logo_upload attempt
  set status = 'ready', updated_at = clock_timestamp()
  where attempt.seller_organization_id = p_seller_organization_id
    and attempt.id = p_upload_id;

  insert into public.audit_event(
    seller_organization_id,actor_identity_user_id,action,target_type,target_id,
    result,request_id,metadata
  ) values(
    p_seller_organization_id,p_identity_user_id,'seller.' || p_kind || '_set',
    'seller_organization',p_seller_organization_id,'succeeded',p_request_id,
    jsonb_build_object(
      'upload_id',p_upload_id,'kind',p_kind,'media_type',p_media_type,'byte_size',p_byte_size,
      'width',p_width,'height',p_height,'idempotency_key',p_idempotency_key
    )
  );
  return next;
end $$
"""

_APAGAR_IMAGEM = r"""
create function app_private.clear_seller_image(
  p_identity_user_id uuid, p_seller_organization_id uuid, p_kind text,
  p_idempotency_key uuid, p_body_sha256 text, p_request_id uuid
) returns table(changed boolean, previous_object_key text)
language plpgsql security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
declare loja public.seller_organization%rowtype;
begin
GUARDA
  if p_kind is null or p_kind not in ('logo','banner')
     or p_idempotency_key is null or p_request_id is null
     or p_body_sha256 is null or p_body_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception 'invalid seller image request' using errcode='22023';
  end if;

  select * into loja from public.seller_organization
  where id=p_seller_organization_id for update;
  if not found then
    raise exception 'seller organization not found' using errcode='P4041';
  end if;
  previous_object_key := case when p_kind = 'logo' then loja.logo_object_key
                              else loja.banner_object_key end;
  if previous_object_key is null then
    changed := false;
    return next;
    return;
  end if;

  if p_kind = 'logo' then
    update public.seller_organization
    set logo_object_key = null, logo_storage_version_id = null,
        logo_media_type = null, logo_sha256 = null,
        logo_width = null, logo_height = null, logo_byte_size = null,
        logo_updated_at = null, updated_at = clock_timestamp()
    where id = p_seller_organization_id;
  else
    update public.seller_organization
    set banner_object_key = null, banner_storage_version_id = null,
        banner_media_type = null, banner_sha256 = null,
        banner_width = null, banner_height = null, banner_byte_size = null,
        banner_updated_at = null, updated_at = clock_timestamp()
    where id = p_seller_organization_id;
  end if;
  changed := true;

  insert into public.audit_event(
    seller_organization_id,actor_identity_user_id,action,target_type,target_id,
    result,request_id,metadata
  ) values(
    p_seller_organization_id,p_identity_user_id,'seller.' || p_kind || '_cleared',
    'seller_organization',p_seller_organization_id,'succeeded',p_request_id,
    jsonb_build_object('kind',p_kind,'idempotency_key',p_idempotency_key)
  );
  return next;
end $$
"""

# O bloco do banner, no mesmo formato do bloco do logo da 0052.
_PROJECAO_LOGO = (
    """    'logo',case when loja.logo_object_key is null then null else jsonb_build_object("""
)
_PROJECAO_BANNER = """    'banner',case when loja.banner_object_key is null then null else jsonb_build_object(
      'object_key',loja.banner_object_key,
      'storage_version_id',loja.banner_storage_version_id,
      'media_type',loja.banner_media_type,'width',loja.banner_width,
      'height',loja.banner_height,'byte_size',loja.banner_byte_size,
      'updated_at',loja.banner_updated_at) end,
"""
_VITRINE_LOGO = """  if seller.logo_object_key is not null then"""
_VITRINE_BANNER = """  if seller.banner_object_key is not null then
    marca := marca || jsonb_build_object('banner',jsonb_build_object(
      'object_key',seller.banner_object_key,
      'storage_version_id',seller.banner_storage_version_id,
      'media_type',seller.banner_media_type,
      'width',seller.banner_width,'height',seller.banner_height));
  end if;
"""


def _com_banner(texto: str, ancora: str, bloco: str) -> str:
    if texto.count(ancora) != 1:
        raise RuntimeError(f"a âncora do banner não aparece exatamente uma vez: {ancora!r}")
    return texto.replace(ancora, bloco + ancora, 1)


def upgrade() -> None:
    m52 = _modulo_0052()
    # DDL de tabela como o migrador (dono das tabelas em ambiente que nasceu
    # antes da convenção, e membro de appsell_owner nos outros) — a lição da
    # 0053. As funções voltam a nascer como appsell_owner logo abaixo.
    op.execute("reset role")
    op.execute(_COLUNAS_DO_BANNER)
    op.execute(_CHECK_DO_BANNER)
    op.execute(_COLUNA_KIND)
    op.execute("drop index app_private.seller_logo_upload_one_pending_idx")
    op.execute(_PENDENTE_POR_TIPO)
    # Em cadeia que subiu numa transação só antes da correção da 0041, a
    # tabela da 0052 nasceu como migrador e as funções definer (dono
    # appsell_owner) não conseguiam tocá-la. Onde já é appsell_owner, é no-op.
    op.execute("alter table app_private.seller_logo_upload owner to appsell_owner")
    op.execute("set local role appsell_owner")
    # As funções do logo saem; as de imagem entram com p_kind.
    for signature in _FUNCOES_DO_LOGO:
        op.execute(f"revoke all on function {signature} from public")
        op.execute(f"drop function {signature}")
    for sql in (_CRIAR_INTENT, _LER_UPLOAD, _FECHAR_UPLOAD, _GRAVAR_IMAGEM, _APAGAR_IMAGEM):
        op.execute(sql.replace("GUARDA", _GUARDA.strip("\n")))
    for signature in _FUNCOES_DA_IMAGEM:
        op.execute(f"alter function {signature} owner to appsell_owner")
        op.execute(f"revoke all on function {signature} from public")
        op.execute(f"grant execute on function {signature} to appsell_runtime")
    # A projeção (chamada por read/update_seller_settings) e a vitrine ganham o
    # banner: mesmo texto da 0052 com o bloco novo.
    op.execute(f"drop function {_PROJECAO_SIGNATURE}")
    op.execute(_com_banner(m52._PROJECAO, _PROJECAO_LOGO, _PROJECAO_BANNER))
    op.execute(f"alter function {_PROJECAO_SIGNATURE} owner to appsell_owner")
    op.execute(f"revoke all on function {_PROJECAO_SIGNATURE} from public")
    op.execute(_com_banner(m52._VITRINE, _VITRINE_LOGO, _VITRINE_BANNER))
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0053)
            .replace("PARA", _READY_ATE_0054)
        )
    op.execute(
        _REESCREVE.replace("ALVO", _READY)
        .replace("DE", _ANCORA_DO_LOGO)
        .replace("PARA", _ANCORA_DA_IMAGEM)
    )


def downgrade() -> None:
    m52 = _modulo_0052()
    op.execute(
        _REESCREVE.replace("ALVO", _READY)
        .replace("DE", _ANCORA_DA_IMAGEM)
        .replace("PARA", _ANCORA_DO_LOGO)
    )
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0054)
            .replace("PARA", _READY_ATE_0053)
        )
    op.execute(m52._VITRINE)
    op.execute(f"drop function {_PROJECAO_SIGNATURE}")
    op.execute(m52._PROJECAO)
    op.execute(f"alter function {_PROJECAO_SIGNATURE} owner to appsell_owner")
    op.execute(f"revoke all on function {_PROJECAO_SIGNATURE} from public")
    for signature in _FUNCOES_DA_IMAGEM:
        op.execute(f"revoke all on function {signature} from public")
        op.execute(f"drop function {signature}")
    for sql in (
        m52._CRIAR_INTENT_DO_LOGO,
        m52._LER_UPLOAD_DO_LOGO,
        m52._FECHAR_UPLOAD_DO_LOGO,
        m52._GRAVAR_LOGO,
        m52._APAGAR_LOGO,
    ):
        op.execute(sql)
    for signature in _FUNCOES_DO_LOGO:
        op.execute(f"alter function {signature} owner to appsell_owner")
        op.execute(f"revoke all on function {signature} from public")
        op.execute(f"grant execute on function {signature} to appsell_runtime")
    op.execute("reset role")
    op.execute("drop index app_private.seller_logo_upload_one_pending_per_kind_idx")
    # Uploads de banner ainda pendentes fecham antes de a coluna sumir: sem o
    # tipo, a chave deles não seria mais reconhecida.
    op.execute(
        "update app_private.seller_logo_upload set status='cancelled', failure_code='downgrade', "
        "updated_at=clock_timestamp() where kind='banner' and status='pending'"
    )
    op.execute(m52._UPLOAD_PENDENTE_UNICO)
    op.execute("alter table app_private.seller_logo_upload drop column kind")
    op.execute(
        "alter table public.seller_organization drop constraint seller_organization_banner_complete"
    )
    op.execute(
        """
        alter table public.seller_organization
          drop column banner_object_key,
          drop column banner_storage_version_id,
          drop column banner_media_type,
          drop column banner_sha256,
          drop column banner_width,
          drop column banner_height,
          drop column banner_byte_size,
          drop column banner_updated_at
        """
    )
    op.execute("set local role appsell_owner")
