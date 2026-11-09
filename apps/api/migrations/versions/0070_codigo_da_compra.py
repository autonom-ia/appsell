"""Código da compra e cópia marcada do PDF baixado (#559, parte B1).

O PDF baixado leva no rodapé o e-mail mascarado do comprador e um código da
compra (decisão do Rodrigo em 05/11/2026). Se a cópia vazar, a loja acha a
compra pelo código, e o e-mail inteiro não circula.

Duas tabelas de loja, só acessíveis por função `security definer`:

* `app_private.purchase_code`: um código por compra (entitlement). São 10
  caracteres de um alfabeto sem os ambíguos (0/O, 1/I/L, U), sorteados com
  `gen_random_bytes` e com rejeição para não enviesar: cerca de 49 bits, nem
  sequencial nem derivado de id. O código é único dentro da loja, e só a loja
  dona o resolve.
* `app_private.watermarked_asset_copy`: a cópia marcada de um PDF por compra e
  versão do asset, guardada no S3 em
  `tenants/<loja>/watermarked/<compra>/<asset>/v<n>-<sha256>.pdf`, no mesmo
  bucket do original. O banco obriga essa chave e esse bucket: a cópia nunca
  sai do prefixo privado da loja. O hash na chave fecha a corrida de dois
  downloads ao mesmo tempo: cada geração cai na própria chave, e a linha que
  fica descreve exatamente os bytes daquela chave.

Funções:

* `prepare_watermarked_download` (comprador, contexto `library.asset.download`):
  passa pela mesma autorização do download (`authorize_publication_asset_download`)
  e só aceita PDF. Garante o código da compra e devolve a cópia em cache, se
  houver, junto com o bucket e o começo da chave onde uma cópia nova deve ficar.
* `record_watermarked_asset_copy` (comprador, mesmo contexto): registra a cópia
  gerada. A primeira gravação vale; uma corrida devolve a que ficou.
* `resolve_purchase_code` (loja, capacidade `seller.purchase_code.resolve`,
  dono ou admin): devolve a compra do código, só dentro da própria loja. O
  código de outra loja volta vazio.

Nenhuma rota chama estas funções ainda: o download passa a usar a cópia na
parte B2, atrás de flag por loja. A retenção e a exclusão da cópia junto com a
conta são a parte C.

A descida recusa enquanto existir código emitido: ele já pode estar impresso em
PDF baixado, e apagá-lo tiraria da loja o jeito de achar a compra.
"""

from __future__ import annotations

from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0070_codigo_da_compra"
down_revision = "0074_acesso_em_disputa"
branch_labels = None
depends_on = None

CAPACIDADE_RESOLVER = "seller.purchase_code.resolve"
CONTEXTO_DOWNLOAD = "library.asset.download"
# Sem 0/O, 1/I/L e U: lido em voz alta ou digitado de um PDF, não confunde.
ALFABETO = "ABCDEFGHJKMNPQRSTVWXYZ23456789"
TAMANHO_DO_CODIGO = 10
# 240 é o maior múltiplo de 30 que cabe num byte: o byte acima disso é
# descartado, para que cada letra saia com a mesma chance.
LIMITE_SEM_VIES = 240
TENTATIVAS_DE_CODIGO = 8
# O teto do PDF no catálogo (`asset_media.SUPPORTED_MEDIA`, 100 MiB), escrito
# aqui como número porque a migration não muda quando o catálogo mudar. A cópia
# marcada de um PDF no limite pode passar do teto por causa do rodapé: o
# registro dela é recusado, e a parte B2 precisa tratar essa recusa.
TETO_DA_COPIA_EM_BYTES = 104_857_600
SEARCH_PATH = "set search_path=pg_catalog,app_private,public,pg_temp"

TABELAS = ("purchase_code", "watermarked_asset_copy")
ASSINATURAS = (
    "app_private.prepare_watermarked_download(uuid,uuid,uuid)",
    "app_private.record_watermarked_asset_copy(uuid,uuid,uuid,integer,text,text,text,bigint)",
    "app_private.resolve_purchase_code(uuid,uuid,text)",
)


def _carregar(revisao: str) -> ModuleType:
    caminho = Path(__file__).with_name(f"{revisao}.py")
    spec = importlib_util.spec_from_file_location(f"appsell_anterior_{revisao}_para_0070", caminho)
    if spec is None or spec.loader is None:  # pragma: no cover - caminho fixo no repo
        raise RuntimeError(f"a migration {revisao} nao pode ser lida")
    modulo = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


# A lista do readiness vem da anterior na cadeia (`_READY_ATE_<n>` dela).
READY_ANTES: str = getattr(_carregar(down_revision), f"_READY_ATE_{down_revision.split('_', 1)[0]}")
READY_DEPOIS = READY_ANTES[:-2] + f",'{revision}'))"
# Nome que as migrations seguintes leem por `getattr` (padrão da 0067 em diante).
_READY_ATE_0070 = READY_DEPOIS
_BASES_DO_READY: tuple[str, ...] = (
    "app_private.schema_ready_base_0016()",
    "app_private.schema_ready_base_0017()",
)
_BASE_DO_ACESSO = "app_private.schema_ready_base_0016()"
# A última migration que acrescentou função ao readiness. As linhas desta
# entram depois das dela, e a próxima lê `_ready_funcoes()` desta.
_ULTIMA_COM_FUNCAO_NO_READY = down_revision

_READY_DO_CODIGO = (
    "            and to_regclass('app_private.purchase_code') is not null\n"
    "            and to_regclass('app_private.watermarked_asset_copy') is not null\n"
    "            and (select count(*) from pg_catalog.pg_class tabela\n"
    "              where tabela.oid in (to_regclass('app_private.purchase_code'),\n"
    "                to_regclass('app_private.watermarked_asset_copy'))\n"
    "                and tabela.relrowsecurity and tabela.relforcerowsecurity) = 2\n"
    "            and to_regprocedure('app_private.prepare_watermarked_download("
    "uuid,uuid,uuid)') is not null\n"
    "            and to_regprocedure('app_private.record_watermarked_asset_copy("
    "uuid,uuid,uuid,integer,text,text,text,bigint)') is not null\n"
    "            and to_regprocedure('app_private.resolve_purchase_code("
    "uuid,uuid,text)') is not null\n"
)


def _ready_funcao() -> tuple[str, str]:
    de: str = _carregar(_ULTIMA_COM_FUNCAO_NO_READY)._ready_funcoes()[1]
    return de, de + _READY_DO_CODIGO


# Nome que a próxima migration com função no readiness lê para achar o fim
# deste bloco: (antes, depois desta).
def _ready_funcoes() -> tuple[str, str]:
    return _ready_funcao()


# Marcadores com sublinhado: o texto trocado pode conter "DE" ou "PARA".
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


_CHECK_CODIGO = f"char_length(code) = {TAMANHO_DO_CODIGO} and translate(code,'{ALFABETO}','') = ''"
_CHECK_SHA = "char_length(sha256) = 64 and translate(sha256,'0123456789abcdef','') = ''"
_CHAVE_DA_COPIA = (
    "'tenants/'||seller_organization_id::text||'/watermarked/'||entitlement_id::text"
    "||'/'||asset_id::text||'/v'||asset_version::text||'-'||sha256||'.pdf'"
)

_CRIA_TABELAS = (
    f"""
    create table app_private.purchase_code (
      seller_organization_id uuid not null,
      entitlement_id uuid not null,
      code text not null check ({_CHECK_CODIGO}),
      created_at timestamptz not null default clock_timestamp(),
      constraint purchase_code_pkey primary key (seller_organization_id,entitlement_id),
      constraint purchase_code_code_key unique (seller_organization_id,code),
      constraint purchase_code_entitlement_fkey
        foreign key (seller_organization_id,entitlement_id)
        references public.buyer_entitlement(seller_organization_id,id) on delete cascade
    )
    """,
    f"""
    create table app_private.watermarked_asset_copy (
      seller_organization_id uuid not null,
      entitlement_id uuid not null,
      asset_id uuid not null,
      asset_version integer not null check (asset_version > 0),
      storage_bucket text not null check (char_length(storage_bucket) between 3 and 63
        and translate(storage_bucket,'abcdefghijklmnopqrstuvwxyz0123456789.-','') = ''),
      object_key text not null,
      sha256 text not null check ({_CHECK_SHA}),
      byte_size bigint not null check (byte_size between 1 and {TETO_DA_COPIA_EM_BYTES}),
      created_at timestamptz not null default clock_timestamp(),
      constraint watermarked_asset_copy_pkey
        primary key (seller_organization_id,entitlement_id,asset_id,asset_version),
      constraint watermarked_asset_copy_tenant_object_key_check
        check (object_key = {_CHAVE_DA_COPIA}),
      constraint watermarked_asset_copy_code_fkey
        foreign key (seller_organization_id,entitlement_id)
        references app_private.purchase_code(seller_organization_id,entitlement_id)
        on delete cascade
    )
    """,
)

# Guarda do comprador: contexto assinado da biblioteca, sem loja, da mesma
# pessoa (`library_context_allowed` confere os três).
_GUARDA_DO_COMPRADOR = f"""
      if p_identity_user_id is null or p_entitlement_id is null or p_asset_id is null
         or not app_private.library_context_allowed(p_identity_user_id,'{CONTEXTO_DOWNLOAD}') then
        raise exception 'watermarked download context is not authorized' using errcode='28000';
      end if;
"""

# O asset autorizado para download desta compra, com a loja dela. A autorização
# é a mesma do download de hoje: compra ativa e no prazo, loja ativa, produto
# publicado, material de apoio com download liberado. Daqui só passa PDF. A
# loja da compra é a do asset publicado: o id da compra sozinho não escolhe a
# loja, nem num seed que repetisse o id em duas lojas.
_ASSET_AUTORIZADO = """
      select autorizado.asset_version,autorizado.media_type,autorizado.storage_bucket,
        entitlement.seller_organization_id
        into v_asset_version,v_media_type,v_bucket,v_seller
      from app_private.authorize_publication_asset_download(
        p_identity_user_id,p_entitlement_id,p_asset_id) autorizado
      join public.buyer_entitlement entitlement
        on entitlement.id=p_entitlement_id
       and entitlement.buyer_identity_user_id=p_identity_user_id
      join public.publication_asset publicado
        on publicado.seller_organization_id=entitlement.seller_organization_id
       and publicado.publication_id=entitlement.publication_id
       and publicado.asset_id=p_asset_id
       and publicado.asset_version=autorizado.asset_version;
      if v_seller is null or v_media_type is distinct from 'application/pdf' then
        raise exception 'watermarked download is unavailable' using errcode='28000';
      end if;
"""

_PREPARA = f"""
create function app_private.prepare_watermarked_download(
  p_identity_user_id uuid,p_entitlement_id uuid,p_asset_id uuid
) returns table(seller_organization_id uuid,purchase_code text,asset_version integer,
  copy_storage_bucket text,copy_key_prefix text,cached_object_key text,cached_sha256 text,
  cached_byte_size bigint)
language plpgsql volatile security definer
{SEARCH_PATH} as $$
#variable_conflict use_column
declare
  v_asset_version integer; v_media_type text; v_bucket text; v_seller uuid; v_code text;
  v_bytes bytea; v_byte integer; v_tentativa integer := 0; v_posicao integer;
begin
{_GUARDA_DO_COMPRADOR}
{_ASSET_AUTORIZADO}
  select codigo.code into v_code from app_private.purchase_code codigo
  where codigo.seller_organization_id=v_seller and codigo.entitlement_id=p_entitlement_id;
  while v_code is null loop
    v_tentativa := v_tentativa + 1;
    if v_tentativa > {TENTATIVAS_DE_CODIGO} then
      raise exception 'purchase code could not be issued' using errcode='55000';
    end if;
    v_code := '';
    while char_length(v_code) < {TAMANHO_DO_CODIGO} loop
      v_bytes := gen_random_bytes(16);
      v_posicao := 0;
      while v_posicao < 16 and char_length(v_code) < {TAMANHO_DO_CODIGO} loop
        v_byte := get_byte(v_bytes,v_posicao);
        if v_byte < {LIMITE_SEM_VIES} then
          v_code := v_code || substr('{ALFABETO}',(v_byte % {len(ALFABETO)})+1,1);
        end if;
        v_posicao := v_posicao + 1;
      end loop;
    end loop;
    begin
      insert into app_private.purchase_code(seller_organization_id,entitlement_id,code)
      values (v_seller,p_entitlement_id,v_code)
      on conflict (seller_organization_id,entitlement_id) do nothing;
    exception when unique_violation then
      -- O mesmo código já é de outra compra da loja: sorteia de novo.
      v_code := null;
      continue;
    end;
    -- Outra transação pode ter emitido o código desta compra antes: vale o dela.
    select codigo.code into v_code from app_private.purchase_code codigo
    where codigo.seller_organization_id=v_seller and codigo.entitlement_id=p_entitlement_id;
  end loop;
  return query
  select v_seller,v_code,v_asset_version,v_bucket,
    'tenants/'||v_seller::text||'/watermarked/'||p_entitlement_id::text||'/'||p_asset_id::text
      ||'/v'||v_asset_version::text||'-',
    copia.object_key,copia.sha256,copia.byte_size
  from (select 1) uma
  left join app_private.watermarked_asset_copy copia
    on copia.seller_organization_id=v_seller and copia.entitlement_id=p_entitlement_id
   and copia.asset_id=p_asset_id and copia.asset_version=v_asset_version;
end $$
"""  # noqa: S608 - só constantes deste módulo entram no texto

_REGISTRA = f"""
create function app_private.record_watermarked_asset_copy(
  p_identity_user_id uuid,p_entitlement_id uuid,p_asset_id uuid,p_asset_version integer,
  p_storage_bucket text,p_object_key text,p_sha256 text,p_byte_size bigint
) returns table(storage_bucket text,object_key text,sha256 text,byte_size bigint)
language plpgsql volatile security definer
{SEARCH_PATH} as $$
#variable_conflict use_column
declare v_asset_version integer; v_media_type text; v_bucket text; v_seller uuid;
begin
{_GUARDA_DO_COMPRADOR}
{_ASSET_AUTORIZADO}
  if p_asset_version is distinct from v_asset_version then
    raise exception 'watermarked copy is for another asset version' using errcode='22023';
  end if;
  -- A cópia mora no bucket do original: um bucket informado pelo chamador
  -- poderia ser de fora e servir outro arquivo como se fosse a cópia.
  if p_storage_bucket is distinct from v_bucket then
    raise exception 'watermarked copy must live in the asset bucket' using errcode='22023';
  end if;
  if not exists (select 1 from app_private.purchase_code codigo
                 where codigo.seller_organization_id=v_seller
                   and codigo.entitlement_id=p_entitlement_id) then
    raise exception 'purchase code was not issued' using errcode='55000';
  end if;
  insert into app_private.watermarked_asset_copy(
    seller_organization_id,entitlement_id,asset_id,asset_version,
    storage_bucket,object_key,sha256,byte_size)
  values (v_seller,p_entitlement_id,p_asset_id,p_asset_version,
    p_storage_bucket,p_object_key,p_sha256,p_byte_size)
  on conflict (seller_organization_id,entitlement_id,asset_id,asset_version) do nothing;
  return query
  select copia.storage_bucket,copia.object_key,copia.sha256,copia.byte_size
  from app_private.watermarked_asset_copy copia
  where copia.seller_organization_id=v_seller and copia.entitlement_id=p_entitlement_id
    and copia.asset_id=p_asset_id and copia.asset_version=p_asset_version;
end $$
"""  # noqa: S608 - só constantes deste módulo entram no texto

# Quem confere uma cópia vazada digita o código como leu: com hífen, espaço ou
# minúscula. A forma é normalizada sem regex, e o que não é do alfabeto volta
# vazio, como código de outra loja.
_RESOLVE = f"""
create function app_private.resolve_purchase_code(
  p_actor uuid,p_seller uuid,p_code text
) returns table(entitlement_id uuid,catalog_product_id uuid,issued_at timestamptz)
language plpgsql stable security definer
{SEARCH_PATH} as $$
#variable_conflict use_column
declare v_code text;
begin
  if p_actor is null or p_seller is null
     or app_private.context_text('capability') is distinct from '{CAPACIDADE_RESOLVER}'
     or app_private.context_uuid('identity_user_id') is distinct from p_actor
     or app_private.context_uuid('seller_organization_id') is distinct from p_seller
     or coalesce(app_private.resolve_seller_membership_role(p_seller),'')
        not in ('seller_owner','seller_admin') then
    raise exception 'purchase code context is not authorized' using errcode='42501';
  end if;
  v_code := upper(replace(replace(coalesce(p_code,''),'-',''),' ',''));
  if char_length(v_code) <> {TAMANHO_DO_CODIGO}
     or translate(v_code,'{ALFABETO}','') <> '' then
    return;
  end if;
  return query
  select codigo.entitlement_id,entitlement.catalog_product_id,codigo.created_at
  from app_private.purchase_code codigo
  join public.buyer_entitlement entitlement
    on entitlement.seller_organization_id=codigo.seller_organization_id
   and entitlement.id=codigo.entitlement_id
  where codigo.seller_organization_id=p_seller and codigo.code=v_code;
end $$
"""  # noqa: S608 - só constantes deste módulo entram no texto

FUNCOES = (_PREPARA, _REGISTRA, _RESOLVE)

_RECUSA_DESCIDA = """
do $migration$
begin
  if exists (select 1 from app_private.purchase_code) then
    raise exception 'purchase codes were issued; they may be printed in downloaded PDFs'
      using errcode='55000';
  end if;
end $migration$
"""


def upgrade() -> None:
    for sql in _CRIA_TABELAS:
        op.execute(sql)
    for tabela in TABELAS:
        nome = f"app_private.{tabela}"
        op.execute(f"alter table {nome} owner to appsell_owner")
        op.execute(f"alter table {nome} enable row level security")
        op.execute(f"alter table {nome} force row level security")
        op.execute(
            f"create policy {tabela}_owner_all on {nome} "
            "to appsell_owner using (true) with check (true)"
        )
        op.execute(f"revoke all on table {nome} from public,appsell_runtime")
    for sql in FUNCOES:
        op.execute(sql)
    for assinatura in ASSINATURAS:
        op.execute(f"alter function {assinatura} owner to appsell_owner")
        op.execute(f"revoke all on function {assinatura} from public")
        op.execute(f"grant execute on function {assinatura} to appsell_runtime")
    for alvo in _BASES_DO_READY:
        op.execute(_reescreve(alvo, READY_ANTES, READY_DEPOIS))
    de, para = _ready_funcao()
    op.execute(_reescreve(_BASE_DO_ACESSO, de, para))


def downgrade() -> None:
    op.execute(_RECUSA_DESCIDA)
    de, para = _ready_funcao()
    op.execute(_reescreve(_BASE_DO_ACESSO, para, de))
    for alvo in _BASES_DO_READY:
        op.execute(_reescreve(alvo, READY_DEPOIS, READY_ANTES))
    for assinatura in reversed(ASSINATURAS):
        op.execute(f"drop function {assinatura}")
    for tabela in reversed(TABELAS):
        op.execute(f"drop table app_private.{tabela}")
