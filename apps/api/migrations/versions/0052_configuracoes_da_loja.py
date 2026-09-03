"""Configurações da loja: marca, texto próprio e a página de vendas (#352).

A loja não tinha onde ser configurada. `seller_organization` guardava slug,
nome e status; nenhuma função a atualizava; a vitrine pública falava a língua
da plataforma ("catálogo isolado e verificável") e desenhava um número no
lugar da capa. Esta migration dá à loja o que ela precisa para parecer dela:

- **cor de destaque, tagline e "sobre"** em `seller_organization`;
- **logo**, com as mesmas oito colunas da capa do produto (0037) e o mesmo
  contrato tudo-ou-nada — a imagem passa pelo mesmo verificador, só troca o
  dono: a loja em vez do produto. Por isso a tabela de upload é própria
  (`seller_logo_upload`): a da capa é presa ao produto por chave e por FK;
- **promessa** (`catalog_product.headline`), a frase da página de vendas;
- **vitrine pública reescrita**: devolve marca, capa, promessa e o currículo
  publicado (títulos de aulas e contagem de materiais, lidos do snapshot da
  publicação vigente — nunca do rascunho). Os campos novos só entram no JSON
  **quando existem** (precedente: `access_mode` na 0031). Isso NÃO basta para
  o web antigo: capa e currículo já existem em produtos publicados, e o
  parser antigo exige chaves exatas. Por isso a ordem de deploy é
  **web → api → migration** — o web novo aceita as duas formas, a API nova
  troca chave por URL antes de responder, e só então o banco passa a
  devolver os campos. A revisão desta migration pegou a ordem invertida.

Quem pode: só `seller_owner` e `seller_admin`, lidos por
`resolve_seller_membership_role` (0025). `seller_editor` e `seller_support`
recebem `42501`. A promessa é campo do produto e segue a regra dos outros
campos do produto (`catalog.draft.update`).

O slug não tem parâmetro em função nenhuma. Ele está na URL pública já
distribuída a alunos; imutável na v1 por decisão.

O que o banco NÃO valida: legibilidade da cor. O contraste é conta de
luminância, feita no BFF ao gravar e na renderização ao pintar, com a mesma
função. Aqui só o formato `#rrggbb` minúsculo.

Ordem de deploy: **web → api → migration** (ver acima). Função nova sem
chamador é inofensiva; coluna nova é nula. Downgrade apaga logos, cor,
tagline, sobre e promessa — os objetos de logo já promovidos ficam órfãos no
bucket servido e precisam de limpeza manual (documentado no runbook do
release). Limites de texto são em caracteres (`length`), como o nome: a tela
promete caracteres, e "á" tem dois bytes.
"""

from __future__ import annotations

from alembic import op

revision = "0052_configuracoes_da_loja"
down_revision = "0051_minha_conta_do_comprador"
branch_labels = None
depends_on = None

_READY = "app_private.schema_ready()"
_BASES_DO_READY: tuple[str, ...] = (
    "app_private.schema_ready_base_0016()",
    "app_private.schema_ready_base_0017()",
)
_READY_ATE_0051 = (
    "'0043_material_role_by_media','0044_pdf_de_trezentas_paginas','0045_envio_em_partes',"
    "'0046_aula_de_meio_giga','0047_cota_de_armazenamento','0048_nome_de_quem_entrou',"
    "'0049_caixa_de_saida_de_email','0050_recibo_do_link_de_turma',"
    "'0051_minha_conta_do_comprador'))"
)
_READY_ATE_0052 = (
    "'0043_material_role_by_media','0044_pdf_de_trezentas_paginas','0045_envio_em_partes',"
    "'0046_aula_de_meio_giga','0047_cota_de_armazenamento','0048_nome_de_quem_entrou',"
    "'0049_caixa_de_saida_de_email','0050_recibo_do_link_de_turma',"
    "'0051_minha_conta_do_comprador','0052_configuracoes_da_loja'))"
)
# A última verificação que a 0051 acrescentou a schema_ready(); as novas entram depois dela.
_ANCORA_DO_READY = (
    "            and to_regprocedure('app_private.read_account_status(uuid)') is not null\n"
)
_READY_COM_A_LOJA = _ANCORA_DO_READY + (
    "            and to_regprocedure('app_private.read_seller_settings(uuid,uuid)') is not null\n"
    "            and to_regprocedure("
    "'app_private.update_seller_settings(uuid,uuid,text,text,text,text,uuid)'"
    ") is not null\n"
    "            and to_regprocedure("
    "'app_private.set_seller_logo(uuid,uuid,uuid,text,text,text,text,integer,integer,bigint,uuid,text,uuid)'"
    ") is not null\n"
    "            and to_regprocedure("
    "'app_private.update_product_headline(uuid,uuid,uuid,text,bigint,uuid,text,uuid)'"
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

_SIGNATURES: tuple[str, ...] = (
    "app_private.read_seller_settings(uuid,uuid)",
    "app_private.update_seller_settings(uuid,uuid,text,text,text,text,uuid)",
    "app_private.create_seller_logo_upload_intent(uuid,uuid,text,bigint,text,text,text,uuid,text,uuid)",
    "app_private.read_seller_logo_upload(uuid,uuid,uuid)",
    "app_private.close_seller_logo_upload(uuid,uuid,uuid,text,text,uuid)",
    "app_private.set_seller_logo(uuid,uuid,uuid,text,text,text,text,integer,integer,bigint,uuid,text,uuid)",
    "app_private.clear_seller_logo(uuid,uuid,uuid,text,uuid)",
    "app_private.update_product_headline(uuid,uuid,uuid,text,bigint,uuid,text,uuid)",
)
_PROJECAO_SIGNATURE = "app_private.seller_settings_projection(public.seller_organization)"

# ---------------------------------------------------------------- colunas

_COLUNAS_DA_LOJA = r"""
alter table public.seller_organization
  add column accent_color text,
  add column tagline text,
  add column about text,
  add column logo_object_key text,
  add column logo_storage_version_id text,
  add column logo_media_type text,
  add column logo_sha256 text,
  add column logo_width integer,
  add column logo_height integer,
  add column logo_byte_size bigint,
  add column logo_updated_at timestamptz
"""

_CHECKS_DA_LOJA: tuple[str, ...] = (
    r"""
    alter table public.seller_organization
      add constraint seller_organization_accent_color_format check (
        accent_color is null or accent_color ~ '^#[0-9a-f]{6}$'
      )
    """,
    r"""
    alter table public.seller_organization
      add constraint seller_organization_tagline_strict check (
        tagline is null or (
          tagline = btrim(tagline) and length(tagline) between 2 and 160
          and tagline !~ '[[:cntrl:]]'
        )
      )
    """,
    r"""
    alter table public.seller_organization
      add constraint seller_organization_about_strict check (
        about is null or (
          about = btrim(about) and length(about) between 2 and 600
          and about !~ '[[:cntrl:]]'
        )
      )
    """,
    # Um logo existe inteiro ou não existe: o mesmo contrato da capa (0037).
    r"""
    alter table public.seller_organization
      add constraint seller_organization_logo_complete check (
        (logo_object_key is null and logo_storage_version_id is null
         and logo_media_type is null and logo_sha256 is null
         and logo_width is null and logo_height is null and logo_byte_size is null
         and logo_updated_at is null)
        or
        (logo_object_key is not null and logo_storage_version_id is not null
         and logo_media_type is not null
         and logo_sha256 is not null and logo_width is not null
         and logo_height is not null and logo_byte_size is not null
         and logo_updated_at is not null
         and logo_media_type in ('image/jpeg','image/png','image/webp')
         and logo_sha256 ~ '^[0-9a-f]{64}$'
         and logo_width between 64 and 4000
         and logo_height between 64 and 4000
         and logo_byte_size between 1 and 5242880
         and length(logo_object_key) between 1 and 400
         and length(logo_storage_version_id) between 1 and 512)
      )
    """,
)

_COLUNA_PROMESSA = r"""
alter table public.catalog_product
  add column headline text,
  add constraint catalog_product_headline_strict check (
    headline is null or (
      headline = btrim(headline) and length(headline) between 2 and 200
      and headline !~ '[[:cntrl:]]'
    )
  )
"""

# O registro do pedido de upload antes de existir byte. Espelho de
# product_cover_upload sem o produto: a chave de quarentena é por loja.
_TABELA_DE_UPLOAD = r"""
create table app_private.seller_logo_upload (
  seller_organization_id uuid not null references public.seller_organization(id),
  id uuid not null,
  identity_user_id uuid not null references public.identity_user(id),
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
  constraint seller_logo_upload_bucket_separation check(quarantine_bucket <> ready_bucket),
  constraint seller_logo_upload_expiry check(expires_at > created_at),
  constraint seller_logo_upload_ready_key unique(ready_bucket,ready_key)
)
"""

_UPLOAD_PENDENTE_UNICO = r"""
create unique index seller_logo_upload_one_pending_idx
  on app_private.seller_logo_upload(seller_organization_id)
  where status = 'pending'
"""

_UPLOAD_EXPIRY_IDX = r"""
create index seller_logo_upload_expiry_idx
  on app_private.seller_logo_upload(seller_organization_id,status,expires_at)
"""

# ---------------------------------------------------------------- funções

# A projeção que as escritas e a leitura devolvem. Uma só, para a tela nunca
# ver duas formas do mesmo objeto. Não é chamada pelo runtime.
_PROJECAO = r"""
create function app_private.seller_settings_projection(loja public.seller_organization)
returns jsonb
language sql immutable
set search_path=pg_catalog as $$
  select jsonb_build_object(
    'id',loja.id,'slug',loja.slug,'display_name',loja.display_name,
    'status',loja.status,
    'tagline',loja.tagline,'about',loja.about,'accent_color',loja.accent_color,
    'logo',case when loja.logo_object_key is null then null else jsonb_build_object(
      'object_key',loja.logo_object_key,
      'storage_version_id',loja.logo_storage_version_id,
      'media_type',loja.logo_media_type,'width',loja.logo_width,
      'height',loja.logo_height,'byte_size',loja.logo_byte_size,
      'updated_at',loja.logo_updated_at) end,
    'updated_at',loja.updated_at)
$$
"""

# A guarda de toda função de configuração: capability exata, ator igual ao do
# contexto, loja igual à do contexto, e papel de dono ou admin. O papel vem de
# resolve_seller_membership_role, que só responde sobre a loja do contexto e
# sobre o próprio chamador. Tudo o mais é 42501 — inclusive editor e suporte,
# que a issue #352 exclui por decisão.
#
# O coalesce não é enfeite: quem não tem membership recebe NULL da 0025, e
# `NULL not in (...)` é NULL — o `if` não dispara e o estranho passa. O teste
# de papéis pegou exatamente isso na primeira execução.
_LER_CONFIGURACOES = r"""
create function app_private.read_seller_settings(
  p_identity_user_id uuid, p_seller_organization_id uuid
) returns jsonb
language plpgsql stable security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
declare loja public.seller_organization%rowtype;
begin
  if p_identity_user_id is null or p_seller_organization_id is null
     or app_private.context_text('capability') is distinct from 'seller.settings.read'
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id') is distinct from p_seller_organization_id
     or coalesce(app_private.resolve_seller_membership_role(p_seller_organization_id), '')
        not in ('seller_owner','seller_admin') then
    raise exception 'seller settings context is not authorized' using errcode='42501';
  end if;
  select * into loja from public.seller_organization where id=p_seller_organization_id;
  if not found then
    raise exception 'seller organization not found' using errcode='P4041';
  end if;
  return app_private.seller_settings_projection(loja);
end $$
"""

_ATUALIZAR_CONFIGURACOES = r"""
create function app_private.update_seller_settings(
  p_identity_user_id uuid, p_seller_organization_id uuid,
  p_display_name text, p_tagline text, p_about text, p_accent_color text,
  p_request_id uuid
) returns jsonb
language plpgsql security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
declare
  antes public.seller_organization%rowtype;
  depois public.seller_organization%rowtype;
  alterados jsonb := '[]'::jsonb;
begin
  if p_identity_user_id is null or p_seller_organization_id is null or p_request_id is null
     or app_private.context_text('capability') is distinct from 'seller.settings.update'
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id') is distinct from p_seller_organization_id
     or coalesce(app_private.resolve_seller_membership_role(p_seller_organization_id), '')
        not in ('seller_owner','seller_admin') then
    raise exception 'seller settings context is not authorized' using errcode='42501';
  end if;
  -- O formato é o do CHECK de cada coluna, repetido aqui para a recusa ter
  -- um código só (22023) em vez de vazar o nome da constraint.
  if p_display_name is null or p_display_name <> btrim(p_display_name)
     or length(p_display_name) not between 2 and 120
     or p_display_name ~ '[[:cntrl:]]'
     or (p_tagline is not null and (p_tagline <> btrim(p_tagline)
         or length(p_tagline) not between 2 and 160 or p_tagline ~ '[[:cntrl:]]'))
     or (p_about is not null and (p_about <> btrim(p_about)
         or length(p_about) not between 2 and 600 or p_about ~ '[[:cntrl:]]'))
     or (p_accent_color is not null and p_accent_color !~ '^#[0-9a-f]{6}$') then
    raise exception 'invalid seller settings' using errcode='22023';
  end if;

  select * into antes from public.seller_organization
  where id=p_seller_organization_id for update;
  if not found then
    raise exception 'seller organization not found' using errcode='P4041';
  end if;
  if antes.status <> 'active' then
    raise exception 'seller organization is not active' using errcode='55000';
  end if;

  if antes.display_name is distinct from p_display_name then
    alterados := alterados || to_jsonb('display_name'::text);
  end if;
  if antes.tagline is distinct from p_tagline then
    alterados := alterados || to_jsonb('tagline'::text);
  end if;
  if antes.about is distinct from p_about then
    alterados := alterados || to_jsonb('about'::text);
  end if;
  if antes.accent_color is distinct from p_accent_color then
    alterados := alterados || to_jsonb('accent_color'::text);
  end if;
  if jsonb_array_length(alterados) = 0 then
    return app_private.seller_settings_projection(antes) || jsonb_build_object('changed',false);
  end if;

  update public.seller_organization
  set display_name=p_display_name, tagline=p_tagline, about=p_about,
      accent_color=p_accent_color, updated_at=clock_timestamp()
  where id=p_seller_organization_id
  returning * into depois;

  -- O nome antigo fica no registro: é o único campo que outras pessoas já
  -- viram (na vitrine e em e-mails) e que pode ser preciso reconstituir.
  insert into public.audit_event(
    seller_organization_id,actor_identity_user_id,action,target_type,target_id,
    result,request_id,metadata
  ) values(
    p_seller_organization_id,p_identity_user_id,'seller.settings.updated',
    'seller_organization',p_seller_organization_id,'succeeded',p_request_id,
    jsonb_build_object(
      'changed',alterados,
      'display_name_before',antes.display_name,'display_name_after',depois.display_name,
      'accent_color_before',antes.accent_color,'accent_color_after',depois.accent_color
    )
  );
  return app_private.seller_settings_projection(depois) || jsonb_build_object('changed',true);
end $$
"""

_CRIAR_INTENT_DO_LOGO = r"""
create function app_private.create_seller_logo_upload_intent(
  p_identity_user_id uuid, p_seller_organization_id uuid,
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
  if p_identity_user_id is null or p_seller_organization_id is null
     or app_private.context_text('capability') is distinct from 'seller.settings.update'
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id') is distinct from p_seller_organization_id
     or coalesce(app_private.resolve_seller_membership_role(p_seller_organization_id), '')
        not in ('seller_owner','seller_admin') then
    raise exception 'seller logo context is not authorized' using errcode='42501';
  end if;
  if p_media_type is null
     or p_media_type not in ('image/jpeg','image/png','image/webp')
     or p_byte_size is null or p_byte_size not between 1 and 5242880
     or p_sha256 is null or p_sha256 !~ '^[0-9a-f]{64}$'
     or p_quarantine_bucket is null or p_ready_bucket is null
     or p_quarantine_bucket = p_ready_bucket
     or p_idempotency_key is null or p_request_id is null
     or p_body_sha256 is null or p_body_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception 'invalid seller logo upload intent' using errcode='22023';
  end if;

  select * into loja from public.seller_organization
  where id=p_seller_organization_id for update;
  if not found then
    raise exception 'seller organization not found' using errcode='P4041';
  end if;
  if loja.status <> 'active' then
    raise exception 'seller organization is not active' using errcode='55000';
  end if;

  -- Uma tentativa anterior é substituída, não enfileirada (como na capa).
  update app_private.seller_logo_upload attempt
  set status = case when attempt.expires_at <= clock_timestamp() then 'expired' else 'cancelled' end,
      failure_code = 'superseded',
      updated_at = clock_timestamp()
  where attempt.seller_organization_id = p_seller_organization_id
    and attempt.status = 'pending';

  quarantine := 'tenants/' || p_seller_organization_id::text || '/quarantine/logo';
  ready := 'tenants/' || p_seller_organization_id::text || '/logo/' || upload_id::text;
  expires := clock_timestamp() + interval '15 minutes';

  insert into app_private.seller_logo_upload(
    seller_organization_id,id,identity_user_id,
    expected_media_type,expected_byte_size,expected_sha256,
    quarantine_bucket,ready_bucket,quarantine_key,ready_key,status,expires_at
  ) values(
    p_seller_organization_id,upload_id,p_identity_user_id,
    p_media_type,p_byte_size,p_sha256,
    p_quarantine_bucket,p_ready_bucket,quarantine,ready,'pending',expires
  );

  insert into public.audit_event(
    seller_organization_id,actor_identity_user_id,action,target_type,target_id,
    result,request_id,metadata
  ) values(
    p_seller_organization_id,p_identity_user_id,'seller.logo_upload_created',
    'seller_organization',p_seller_organization_id,'succeeded',p_request_id,
    jsonb_build_object(
      'upload_id',upload_id,'media_type',p_media_type,'byte_size',p_byte_size,
      'idempotency_key',p_idempotency_key
    )
  );

  return jsonb_build_object(
    'upload_id',upload_id,'quarantine_bucket',p_quarantine_bucket,
    'quarantine_key',quarantine,'ready_key',ready,
    'media_type',p_media_type,'byte_size',p_byte_size,'sha256',p_sha256,
    'expires_at',expires
  );
end $$
"""

_LER_UPLOAD_DO_LOGO = r"""
create function app_private.read_seller_logo_upload(
  p_identity_user_id uuid, p_seller_organization_id uuid, p_upload_id uuid
) returns jsonb
language plpgsql security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
declare found_upload app_private.seller_logo_upload%rowtype;
begin
  if p_identity_user_id is null or p_seller_organization_id is null
     or app_private.context_text('capability') is distinct from 'seller.settings.update'
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id') is distinct from p_seller_organization_id
     or coalesce(app_private.resolve_seller_membership_role(p_seller_organization_id), '')
        not in ('seller_owner','seller_admin') then
    raise exception 'seller logo context is not authorized' using errcode='42501';
  end if;
  select * into found_upload from app_private.seller_logo_upload attempt
  where attempt.seller_organization_id = p_seller_organization_id
    and attempt.id = p_upload_id;
  if not found then
    raise exception 'seller logo upload not found' using errcode='P4041';
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

_FECHAR_UPLOAD_DO_LOGO = r"""
create function app_private.close_seller_logo_upload(
  p_identity_user_id uuid, p_seller_organization_id uuid,
  p_upload_id uuid, p_status text, p_failure_code text, p_request_id uuid
) returns jsonb
language plpgsql security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
declare closed app_private.seller_logo_upload%rowtype;
begin
  if p_identity_user_id is null or p_seller_organization_id is null
     or app_private.context_text('capability') is distinct from 'seller.settings.update'
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id') is distinct from p_seller_organization_id
     or coalesce(app_private.resolve_seller_membership_role(p_seller_organization_id), '')
        not in ('seller_owner','seller_admin') then
    raise exception 'seller logo context is not authorized' using errcode='42501';
  end if;
  if p_status is null or p_status not in ('rejected','cancelled','expired')
     or p_request_id is null
     or (p_failure_code is not null
         and octet_length(p_failure_code) not between 2 and 80) then
    raise exception 'invalid seller logo upload closure' using errcode='22023';
  end if;

  select * into closed from app_private.seller_logo_upload attempt
  where attempt.seller_organization_id = p_seller_organization_id
    and attempt.id = p_upload_id
  for update;
  if not found then
    raise exception 'seller logo upload not found' using errcode='P4041';
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
    p_seller_organization_id,p_identity_user_id,'seller.logo_upload_closed',
    'seller_organization',p_seller_organization_id,
    case when p_status = 'rejected' then 'failed' else 'succeeded' end,
    p_request_id,
    jsonb_build_object('upload_id',p_upload_id,'status',p_status,'failure_code',p_failure_code)
  );
  return jsonb_build_object('upload_id',p_upload_id,'status',p_status,'changed',true);
end $$
"""

_GRAVAR_LOGO = r"""
create function app_private.set_seller_logo(
  p_identity_user_id uuid, p_seller_organization_id uuid,
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
  if p_identity_user_id is null or p_seller_organization_id is null
     or app_private.context_text('capability') is distinct from 'seller.settings.update'
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id') is distinct from p_seller_organization_id
     or coalesce(app_private.resolve_seller_membership_role(p_seller_organization_id), '')
        not in ('seller_owner','seller_admin') then
    raise exception 'seller logo context is not authorized' using errcode='42501';
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
     or p_upload_id is null
     or p_idempotency_key is null or p_request_id is null
     or p_body_sha256 is null or p_body_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception 'invalid seller logo request' using errcode='22023';
  end if;

  select * into locked_upload from app_private.seller_logo_upload attempt
  where attempt.seller_organization_id = p_seller_organization_id
    and attempt.id = p_upload_id
  for update;
  if not found then
    raise exception 'seller logo upload not found' using errcode='P4041';
  end if;
  if locked_upload.status <> 'pending' then
    raise exception 'seller logo upload is closed' using errcode='P4229';
  end if;
  -- A chave é a do intent, nunca a que o chamador quis (como na capa).
  if locked_upload.ready_key <> p_object_key then
    raise exception 'seller logo key is not the reserved one' using errcode='28000';
  end if;
  if locked_upload.expected_media_type <> p_media_type then
    raise exception 'seller logo media type differs from the upload intent'
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

  previous_object_key := loja.logo_object_key;

  update public.seller_organization
  set logo_object_key = p_object_key,
      logo_storage_version_id = p_storage_version_id,
      logo_media_type = p_media_type,
      logo_sha256 = p_sha256,
      logo_width = p_width,
      logo_height = p_height,
      logo_byte_size = p_byte_size,
      logo_updated_at = clock_timestamp(),
      updated_at = clock_timestamp()
  where id = p_seller_organization_id;

  update app_private.seller_logo_upload attempt
  set status = 'ready', updated_at = clock_timestamp()
  where attempt.seller_organization_id = p_seller_organization_id
    and attempt.id = p_upload_id;

  insert into public.audit_event(
    seller_organization_id,actor_identity_user_id,action,target_type,target_id,
    result,request_id,metadata
  ) values(
    p_seller_organization_id,p_identity_user_id,'seller.logo_set',
    'seller_organization',p_seller_organization_id,'succeeded',p_request_id,
    jsonb_build_object(
      'upload_id',p_upload_id,'media_type',p_media_type,'byte_size',p_byte_size,
      'width',p_width,'height',p_height,'idempotency_key',p_idempotency_key
    )
  );
  return next;
end $$
"""

_APAGAR_LOGO = r"""
create function app_private.clear_seller_logo(
  p_identity_user_id uuid, p_seller_organization_id uuid,
  p_idempotency_key uuid, p_body_sha256 text, p_request_id uuid
) returns table(changed boolean, previous_object_key text)
language plpgsql security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
declare loja public.seller_organization%rowtype;
begin
  if p_identity_user_id is null or p_seller_organization_id is null
     or app_private.context_text('capability') is distinct from 'seller.settings.update'
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id') is distinct from p_seller_organization_id
     or coalesce(app_private.resolve_seller_membership_role(p_seller_organization_id), '')
        not in ('seller_owner','seller_admin') then
    raise exception 'seller logo context is not authorized' using errcode='42501';
  end if;
  if p_idempotency_key is null or p_request_id is null
     or p_body_sha256 is null or p_body_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception 'invalid seller logo request' using errcode='22023';
  end if;

  select * into loja from public.seller_organization
  where id=p_seller_organization_id for update;
  if not found then
    raise exception 'seller organization not found' using errcode='P4041';
  end if;
  if loja.logo_object_key is null then
    changed := false;
    previous_object_key := null;
    return next;
    return;
  end if;

  previous_object_key := loja.logo_object_key;
  update public.seller_organization
  set logo_object_key = null, logo_storage_version_id = null,
      logo_media_type = null, logo_sha256 = null,
      logo_width = null, logo_height = null, logo_byte_size = null,
      logo_updated_at = null, updated_at = clock_timestamp()
  where id = p_seller_organization_id;
  changed := true;

  insert into public.audit_event(
    seller_organization_id,actor_identity_user_id,action,target_type,target_id,
    result,request_id,metadata
  ) values(
    p_seller_organization_id,p_identity_user_id,'seller.logo_cleared',
    'seller_organization',p_seller_organization_id,'succeeded',p_request_id,
    jsonb_build_object('idempotency_key',p_idempotency_key)
  );
  return next;
end $$
"""

# A promessa é campo do produto e segue a regra dos outros campos do produto:
# capability catalog.draft.update, versão esperada, e um só caminho de escrita.
# Não entra no snapshot da publicação: como a capa, é "ao vivo" — trocar a
# frase da página de vendas não exige republicar o curso.
_PROMESSA = r"""
create function app_private.update_product_headline(
  p_identity_user_id uuid, p_seller_organization_id uuid, p_catalog_product_id uuid,
  p_headline text, p_expected_product_version bigint,
  p_idempotency_key uuid, p_body_sha256 text, p_request_id uuid
) returns table(product_version bigint, changed boolean)
language plpgsql security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
declare locked_product public.catalog_product%rowtype;
begin
  if app_private.context_text('capability') is distinct from 'catalog.draft.update'
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id') is distinct from
        p_seller_organization_id then
    raise exception 'product headline context is not authorized' using errcode='28000';
  end if;
  if p_expected_product_version is null or p_expected_product_version < 1
     or p_idempotency_key is null or p_request_id is null
     or p_body_sha256 is null or p_body_sha256 !~ '^[0-9a-f]{64}$'
     or (p_headline is not null and (p_headline <> btrim(p_headline)
         or length(p_headline) not between 2 and 200 or p_headline ~ '[[:cntrl:]]')) then
    raise exception 'invalid product headline request' using errcode='22023';
  end if;

  select * into locked_product from public.catalog_product product
  where product.seller_organization_id = p_seller_organization_id
    and product.id = p_catalog_product_id
  for update;
  if not found then
    raise exception 'catalog product not found' using errcode='P4041';
  end if;
  if locked_product.status = 'archived' then
    raise exception 'archived product cannot take a headline' using errcode='P4228';
  end if;
  if locked_product.version <> p_expected_product_version then
    raise exception 'catalog product version conflict' using errcode='40001';
  end if;
  if locked_product.headline is not distinct from p_headline then
    product_version := locked_product.version;
    changed := false;
    return next;
    return;
  end if;

  update public.catalog_product product
  set headline = p_headline, version = product.version + 1, updated_at = now()
  where product.seller_organization_id = p_seller_organization_id
    and product.id = p_catalog_product_id
  returning product.version into product_version;
  changed := true;

  insert into public.audit_event(
    seller_organization_id,actor_identity_user_id,action,target_type,target_id,
    result,request_id,metadata
  ) values(
    p_seller_organization_id,p_identity_user_id,'catalog.product_headline_updated',
    'catalog_product',p_catalog_product_id,'succeeded',p_request_id,
    jsonb_build_object(
      'headline_before',locked_product.headline,'headline_after',p_headline,
      'idempotency_key',p_idempotency_key
    )
  );
  return next;
end $$
"""

# ---------------------------------------------------------------- vitrine

# A vitrine reescrita. O que muda em relação à 0031:
# - `seller` ganha tagline, about, accent_color e logo — cada um SÓ quando
#   existe (ver docstring: o parser do web exige chaves exatas);
# - cada produto ganha cover, headline e curriculum — idem;
# - o currículo vem do snapshot da publicação vigente (schema 2..4), nunca das
#   tabelas de rascunho: só títulos de aula e contagem de materiais. Nada do
#   corpo da aula, nada de arquivo, nada de id.
# O que o BFF recebe em logo/cover é chave e versão de objeto; quem os troca por
# URL presignada é public_routes.py, e é lá que a chave morre.
_VITRINE = r"""
create or replace function app_private.read_public_storefront(p_seller_slug text)
returns jsonb language plpgsql stable security definer
set search_path=pg_catalog,app_private,public as $$
declare seller public.seller_organization%rowtype; products jsonb; marca jsonb;
begin
  if p_seller_slug is null or p_seller_slug !~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'
     or octet_length(p_seller_slug) not between 3 and 63 then
    return null;
  end if;
  select * into seller
  from seller_organization organization
  where organization.slug=p_seller_slug and organization.status='active';
  if not found then return null; end if;

  select coalesce(jsonb_agg(
    jsonb_build_object(
      'id',product.id,
      'slug',publication.snapshot->'product'->>'slug',
      'title',publication.snapshot->'product'->>'title',
      'description',coalesce(publication.snapshot->'product'->>'description',''),
      'publication_id',publication.id,
      'publication_version',publication.version,
      'access_mode',case when offer.status='active' then 'paid' else 'free' end,
      'price',case when offer.status='active' and offer.unit_amount_minor > 0
        then jsonb_build_object(
          'unit_amount_minor',offer.unit_amount_minor,
          'currency',rtrim(offer.currency),
          'billing_period',offer.billing_period
        )
        else null end
    )
    || case when product.headline is null then '{}'::jsonb
         else jsonb_build_object('headline',product.headline) end
    || case when product.cover_object_key is null then '{}'::jsonb
         else jsonb_build_object('cover',jsonb_build_object(
           'object_key',product.cover_object_key,
           'storage_version_id',product.cover_storage_version_id,
           'media_type',product.cover_media_type,
           'width',product.cover_width,'height',product.cover_height)) end
    || app_private.published_curriculum_summary(publication.snapshot)
    order by product.created_at,product.id),'[]'::jsonb)
  into products
  from catalog_product product
  join publication
    on publication.seller_organization_id=product.seller_organization_id
   and publication.catalog_product_id=product.id
   and publication.id=product.published_snapshot_id
  join offer
    on offer.seller_organization_id=product.seller_organization_id
   and offer.catalog_product_id=product.id
  where product.seller_organization_id=seller.id
    and product.status='published'
    and offer.status <> 'archived';

  marca := '{}'::jsonb;
  if seller.tagline is not null then
    marca := marca || jsonb_build_object('tagline',seller.tagline);
  end if;
  if seller.about is not null then
    marca := marca || jsonb_build_object('about',seller.about);
  end if;
  if seller.accent_color is not null then
    marca := marca || jsonb_build_object('accent_color',seller.accent_color);
  end if;
  if seller.logo_object_key is not null then
    marca := marca || jsonb_build_object('logo',jsonb_build_object(
      'object_key',seller.logo_object_key,
      'storage_version_id',seller.logo_storage_version_id,
      'media_type',seller.logo_media_type,
      'width',seller.logo_width,'height',seller.logo_height));
  end if;

  return jsonb_build_object(
    'schema_version',1,
    'seller',jsonb_build_object(
      'id',seller.id,'slug',seller.slug,'name',seller.display_name
    ) || marca,
    'products',products
  );
end $$
"""

# O bloco "o que você vai receber", lido do snapshot da publicação. Devolve
# `{}` quando não há aula com título (produto sem currículo, ou snapshot de
# schema anterior ao 2): a chave só existe quando há o que mostrar. No máximo
# 40 títulos — é uma prévia, não o índice completo; as contagens são inteiras.
_RESUMO_DO_CURRICULO = r"""
create function app_private.published_curriculum_summary(p_snapshot jsonb)
returns jsonb
language sql immutable
set search_path=pg_catalog as $$
  with aulas as (
    select lesson->>'title' as titulo,
           jsonb_array_length(coalesce(lesson->'assets','[]'::jsonb)) as materiais,
           row_number() over () as ordem
    from jsonb_array_elements(
           case when jsonb_typeof(p_snapshot->'courses') = 'array'
                then p_snapshot->'courses' else '[]'::jsonb end) course,
         jsonb_array_elements(
           case when jsonb_typeof(course->'modules') = 'array'
                then course->'modules' else '[]'::jsonb end) module,
         jsonb_array_elements(
           case when jsonb_typeof(module->'lessons') = 'array'
                then module->'lessons' else '[]'::jsonb end) lesson
    where lesson->>'title' is not null
  )
  select case when count(*) = 0 then '{}'::jsonb
    else jsonb_build_object('curriculum',jsonb_build_object(
      'lesson_count',count(*),
      'material_count',coalesce(sum(materiais),0),
      'lessons',coalesce((select jsonb_agg(titulo order by ordem)
                          from (select titulo,ordem from aulas order by ordem limit 40) previa),
                         '[]'::jsonb)
    )) end
  from aulas
$$
"""
_RESUMO_SIGNATURE = "app_private.published_curriculum_summary(jsonb)"

# O texto da 0031, verbatim, para o downgrade devolver a vitrine ao que era.
_VITRINE_ANTERIOR = """
create or replace function app_private.read_public_storefront(p_seller_slug text)
returns jsonb language plpgsql stable security definer
set search_path=pg_catalog,app_private,public as $$
declare seller record; products jsonb;
begin
  if p_seller_slug is null or p_seller_slug !~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'
     or octet_length(p_seller_slug) not between 3 and 63 then
    return null;
  end if;
  select organization.id,organization.slug,organization.display_name
  into seller
  from seller_organization organization
  where organization.slug=p_seller_slug and organization.status='active';
  if not found then return null; end if;

  select coalesce(jsonb_agg(jsonb_build_object(
    'id',product.id,
    'slug',publication.snapshot->'product'->>'slug',
    'title',publication.snapshot->'product'->>'title',
    'description',coalesce(publication.snapshot->'product'->>'description',''),
    'publication_id',publication.id,
    'publication_version',publication.version,
    'access_mode',case when offer.status='active' then 'paid' else 'free' end,
    'price',case when offer.status='active' and offer.unit_amount_minor > 0
      then jsonb_build_object(
        'unit_amount_minor',offer.unit_amount_minor,
        'currency',rtrim(offer.currency),
        'billing_period',offer.billing_period
      )
      else null end
  ) order by product.created_at,product.id),'[]'::jsonb)
  into products
  from catalog_product product
  join publication
    on publication.seller_organization_id=product.seller_organization_id
   and publication.catalog_product_id=product.id
   and publication.id=product.published_snapshot_id
  join offer
    on offer.seller_organization_id=product.seller_organization_id
   and offer.catalog_product_id=product.id
  where product.seller_organization_id=seller.id
    and product.status='published'
    and offer.status <> 'archived';

  return jsonb_build_object(
    'schema_version',1,
    'seller',jsonb_build_object(
      'id',seller.id,'slug',seller.slug,'name',seller.display_name
    ),
    'products',products
  );
end $$
"""


def upgrade() -> None:
    op.execute(_COLUNAS_DA_LOJA)
    for check in _CHECKS_DA_LOJA:
        op.execute(check)
    op.execute(_COLUNA_PROMESSA)
    op.execute(_TABELA_DE_UPLOAD)
    op.execute(_UPLOAD_PENDENTE_UNICO)
    op.execute(_UPLOAD_EXPIRY_IDX)
    # As duas auxiliares não entram em _SIGNATURES: não são chamadas pelo
    # runtime, só pelas funções desta migration (que são security definer).
    for auxiliar, signature in (
        (_PROJECAO, _PROJECAO_SIGNATURE),
        (_RESUMO_DO_CURRICULO, _RESUMO_SIGNATURE),
    ):
        op.execute(auxiliar)
        op.execute(f"alter function {signature} owner to appsell_owner")
        op.execute(f"revoke all on function {signature} from public")
    for sql in (
        _LER_CONFIGURACOES,
        _ATUALIZAR_CONFIGURACOES,
        _CRIAR_INTENT_DO_LOGO,
        _LER_UPLOAD_DO_LOGO,
        _FECHAR_UPLOAD_DO_LOGO,
        _GRAVAR_LOGO,
        _APAGAR_LOGO,
        _PROMESSA,
    ):
        op.execute(sql)
    for signature in _SIGNATURES:
        # Dono explícito: o `set local role` do env.py não vale na hora do
        # create, e o downgrade sob outro papel não conseguiria revogar (0051).
        op.execute(f"alter function {signature} owner to appsell_owner")
        op.execute(f"revoke all on function {signature} from public")
        op.execute(f"grant execute on function {signature} to appsell_runtime")
    # `create or replace` preserva dono e grants da 0031.
    op.execute(_VITRINE)
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0051)
            .replace("PARA", _READY_ATE_0052)
        )
    op.execute(
        _REESCREVE.replace("ALVO", _READY)
        .replace("DE", _ANCORA_DO_READY)
        .replace("PARA", _READY_COM_A_LOJA)
    )


def downgrade() -> None:
    op.execute(
        _REESCREVE.replace("ALVO", _READY)
        .replace("DE", _READY_COM_A_LOJA)
        .replace("PARA", _ANCORA_DO_READY)
    )
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0052)
            .replace("PARA", _READY_ATE_0051)
        )
    op.execute(_VITRINE_ANTERIOR)
    for signature in reversed(_SIGNATURES):
        op.execute(f"revoke all on function {signature} from public")
        op.execute(f"drop function {signature}")
    for signature in (_RESUMO_SIGNATURE, _PROJECAO_SIGNATURE):
        op.execute(f"drop function {signature}")
    op.execute("drop table app_private.seller_logo_upload")
    op.execute("alter table public.catalog_product drop constraint catalog_product_headline_strict")
    op.execute("alter table public.catalog_product drop column headline")
    for constraint in (
        "seller_organization_logo_complete",
        "seller_organization_about_strict",
        "seller_organization_tagline_strict",
        "seller_organization_accent_color_format",
    ):
        op.execute(f"alter table public.seller_organization drop constraint {constraint}")
    # Os audit_event ficam (append-only). Os objetos de logo já promovidos ficam
    # órfãos no bucket servido: limpeza manual, documentada no runbook.
    op.execute(
        """
        alter table public.seller_organization
          drop column accent_color,
          drop column tagline,
          drop column about,
          drop column logo_object_key,
          drop column logo_storage_version_id,
          drop column logo_media_type,
          drop column logo_sha256,
          drop column logo_width,
          drop column logo_height,
          drop column logo_byte_size,
          drop column logo_updated_at
        """
    )
