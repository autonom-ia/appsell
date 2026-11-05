"""Links UTM da loja e atribuição da venda à última visita em 7 dias (#563).

Decisão do Rodrigo em 05/11/2026, opção B: cookie de 7 dias no próprio domínio
da loja, gravado só depois do "Aceitar" no banner. A recusa é o padrão. Esta
migration cria o que fica no Sell:

- `campaign_link`: o link que a loja monta, com os campos UTM e a página de
  destino (a vitrine, ou uma página de produto pelo slug);
- `checkout_attribution`: a atribuição gravada no início do checkout, presa ao
  sha256 da compra. É a mesma chave que o ingress do Financial grava em
  `webhook_event.external_purchase_id_sha256`, e é por ela que a venda
  aprovada encontra a campanha. É dado **pseudônimo**, não anônimo: o sha da
  compra sem sal liga a atribuição a uma compra. Não guarda e-mail,
  identidade, IP nem id de visitante, e guarda a versão do texto do banner
  que a pessoa aceitou (prova do consentimento);
- `cookie_consent_tally`: contagem diária de aceites e recusas por loja e
  versão do texto. Serve de prova agregada e de taxa de aceite, sem dado
  pessoal.

A atribuição só chega aqui quando o BFF confirma o aceite da versão atual do
texto e o cookie está na janela de 7 dias (`lib/tracking/`). A função confere
de novo a janela e a loja do link.

Retenção (#563): a atribuição com venda aprovada fica 13 meses; sem venda
aprovada em 48 h, é apagada. A regra nasce junto com o dado, nesta mesma
migration (`purge_checkout_attribution`), porque a promessa da LGPD vale desde
o primeiro registro. Quem chama é o worker de retenção da API, a cada hora; a
trava de transação garante que só uma réplica limpe por vez.

Todas as tabelas: RLS `enable` + `force`, política só para o dono, `revoke
all`, acesso só por função `security definer` com `pg_temp` no fim do
`search_path`, e a migration entra no `schema_ready`. PK com
`seller_organization_id`.

O `downgrade` apaga links, atribuições e contagens.

Revision ID: 0075_links_e_atribuicao_utm
Revises: 0066_consentimento_de_email
"""

from __future__ import annotations

from alembic import op

revision = "0075_links_e_atribuicao_utm"
down_revision = "0066_consentimento_de_email"
branch_labels = None
depends_on = None

CAPACIDADE_LEITURA = "growth.campaign_link.read"
CAPACIDADE_ESCRITA = "growth.campaign_link.write"
JANELA_DIAS = 7
MAXIMO_DE_LINKS_ATIVOS = 200
MAXIMO_DE_LINKS_LISTADOS = 500
HORAS_SEM_VENDA = 48
MESES_DE_RETENCAO = 13
LOTE_MAXIMO_DA_LIMPEZA = 5000
PERIODO_MAXIMO_DIAS = 366
TAMANHO_UTM = 100
TAMANHO_NOME = 80
FUSO = "America/Sao_Paulo"
CAMPOS_UTM = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")

READY_ANTES = (
    "'0057_banner_na_vitrine','0058_capa_na_biblioteca','0059_canal_financial_global',"
    "'0060_atualizacao_chega_ao_aluno','0061_o_atraso_alcanca_o_aluno',"
    "'0062_mapping_do_produto_novo','0063_conciliacao_do_acesso',"
    "'0064_vendas_ativadas_pela_loja','0065_dono_da_conta_payout',"
    "'0066_consentimento_de_email'))"
)
READY_DEPOIS = (
    "'0057_banner_na_vitrine','0058_capa_na_biblioteca','0059_canal_financial_global',"
    "'0060_atualizacao_chega_ao_aluno','0061_o_atraso_alcanca_o_aluno',"
    "'0062_mapping_do_produto_novo','0063_conciliacao_do_acesso',"
    "'0064_vendas_ativadas_pela_loja','0065_dono_da_conta_payout',"
    "'0066_consentimento_de_email','0075_links_e_atribuicao_utm'))"
)
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

SEARCH_PATH = "set search_path=pg_catalog,app_private,public,pg_temp"

TABELAS = ("campaign_link", "checkout_attribution", "cookie_consent_tally")

# Campo UTM: aparado, de 1 a 100 bytes, sem caractere de controle. A conferência
# é por translate (sem regex): tirar os caracteres de controle não pode mudar
# o texto.
_CHECK_UTM = (
    "check (__CAMPO__ is null or (__CAMPO__ = btrim(__CAMPO__) "
    "and octet_length(__CAMPO__) between 1 and __TAMANHO_UTM__ "
    "and translate(__CAMPO__, __CONTROLE__, '') = __CAMPO__))"
)
_CONTROLE = (
    "chr(1)||chr(2)||chr(3)||chr(4)||chr(5)||chr(6)||chr(7)||chr(8)||chr(9)||chr(10)||"
    "chr(11)||chr(12)||chr(13)||chr(14)||chr(15)||chr(16)||chr(17)||chr(18)||chr(19)||"
    "chr(20)||chr(21)||chr(22)||chr(23)||chr(24)||chr(25)||chr(26)||chr(27)||chr(28)||"
    "chr(29)||chr(30)||chr(31)||chr(127)"
)


def _checks_utm() -> str:
    return ",\n      ".join(
        _CHECK_UTM.replace("__CAMPO__", campo)
        .replace("__TAMANHO_UTM__", str(TAMANHO_UTM))
        .replace("__CONTROLE__", _CONTROLE)
        for campo in CAMPOS_UTM
    )


_COLUNAS_UTM = ",\n      ".join(f"{campo} text" for campo in CAMPOS_UTM)

_CRIA_TABELAS_MOLDE = (
    """
    create table app_private.campaign_link (
      seller_organization_id uuid not null references public.seller_organization(id),
      id uuid not null default gen_random_uuid(),
      name text not null check (name = btrim(name) and char_length(name) between 1 and __NOME__),
      -- null é a vitrine; senão, o slug de uma página de produto da loja.
      destination_page_slug text check (destination_page_slug is null or (
        octet_length(destination_page_slug) between 1 and 120
        and translate(destination_page_slug,'abcdefghijklmnopqrstuvwxyz0123456789-','') = '')),
      __COLUNAS_UTM__,
      status text not null default 'active' check (status in ('active','archived')),
      created_by uuid references public.identity_user(id) on delete set null,
      created_at timestamptz not null default clock_timestamp(),
      archived_at timestamptz,
      primary key (seller_organization_id,id),
      check (utm_source is not null),
      check ((status = 'archived') = (archived_at is not null)),
      __CHECKS_UTM__
    )
    """,
    """
    create table app_private.checkout_attribution (
      seller_organization_id uuid not null references public.seller_organization(id),
      purchase_sha256 text not null check (octet_length(purchase_sha256) = 64
        and translate(purchase_sha256,'0123456789abcdef','') = ''),
      link_id uuid,
      __COLUNAS_UTM__,
      visited_at timestamptz not null,
      -- A versão do texto do banner que a pessoa aceitou: prova de qual texto
      -- autorizou esta gravação.
      consent_text_version text not null check (octet_length(consent_text_version) between 1 and 16
        and translate(consent_text_version,'abcdefghijklmnopqrstuvwxyz0123456789.','') = ''),
      created_at timestamptz not null default clock_timestamp(),
      primary key (seller_organization_id,purchase_sha256),
      foreign key (seller_organization_id,link_id)
        references app_private.campaign_link(seller_organization_id,id),
      check (link_id is not null or utm_source is not null or utm_medium is not null
        or utm_campaign is not null or utm_content is not null or utm_term is not null),
      __CHECKS_UTM__
    )
    """,
    "create index checkout_attribution_created_idx "
    "on app_private.checkout_attribution (created_at)",
    """
    create table app_private.cookie_consent_tally (
      seller_organization_id uuid not null references public.seller_organization(id),
      day date not null,
      text_version text not null check (octet_length(text_version) between 1 and 16
        and translate(text_version,'abcdefghijklmnopqrstuvwxyz0123456789.','') = ''),
      accepted integer not null default 0 check (accepted >= 0),
      refused integer not null default 0 check (refused >= 0),
      primary key (seller_organization_id,day,text_version)
    )
    """,
)

# Guarda das funções do painel: contexto assinado da mesma pessoa e loja, com
# a capacidade certa, e papel de dono ou admin.
_GUARDA = """
      if p_actor is null or p_seller is null
         or app_private.context_text('capability') is distinct from '__CAPACIDADE__'
         or app_private.context_uuid('identity_user_id') is distinct from p_actor
         or app_private.context_uuid('seller_organization_id') is distinct from p_seller
         or coalesce(app_private.resolve_seller_membership_role(p_seller),'')
            not in ('seller_owner','seller_admin') then
        raise exception 'campaign link context is not authorized' using errcode='42501';
      end if;
"""

_JSON_DO_LINK = """jsonb_build_object('id',link.id,'name',link.name,
          'destination_page_slug',link.destination_page_slug,
          'utm_source',link.utm_source,'utm_medium',link.utm_medium,
          'utm_campaign',link.utm_campaign,'utm_content',link.utm_content,
          'utm_term',link.utm_term,'status',link.status,'created_at',link.created_at)"""

_FUNCOES_MOLDE = (
    """
    create function app_private.create_campaign_link(
      p_actor uuid,p_seller uuid,p_name text,p_destination_page_slug text,
      p_utm_source text,p_utm_medium text,p_utm_campaign text,p_utm_content text,
      p_utm_term text,p_request uuid
    ) returns jsonb language plpgsql volatile security definer
    __SEARCH_PATH__ as $$
    declare link app_private.campaign_link;
    begin
__GUARDA_ESCRITA__
      if p_request is null then
        raise exception 'campaign link context is not authorized' using errcode='42501';
      end if;
      -- Trava por loja: dois pedidos ao mesmo tempo não passam juntos do teto.
      perform pg_advisory_xact_lock(hashtextextended('appsell.campaign_link:' || p_seller::text, 0));
      if (select count(*) from app_private.campaign_link existing
          where existing.seller_organization_id=p_seller and existing.status='active')
         >= __MAXIMO_ATIVOS__ then
        raise exception 'too many campaign links' using errcode='P4130';
      end if;
      if p_destination_page_slug is not null and not exists (
          select 1 from public.catalog_product product
          where product.seller_organization_id=p_seller and product.slug=p_destination_page_slug) then
        raise exception 'invalid campaign link destination' using errcode='22023';
      end if;
      insert into app_private.campaign_link(seller_organization_id,name,destination_page_slug,
        utm_source,utm_medium,utm_campaign,utm_content,utm_term,created_by)
      values(p_seller,p_name,p_destination_page_slug,p_utm_source,p_utm_medium,p_utm_campaign,
        p_utm_content,p_utm_term,p_actor)
      returning * into link;
      insert into public.audit_event(seller_organization_id,actor_identity_user_id,
        action,target_type,target_id,result,request_id,metadata)
      values(p_seller,p_actor,'growth.campaign_link_created','campaign_link',link.id,
        'succeeded',p_request,'{}'::jsonb);
      return __JSON_DO_LINK__;
    exception when check_violation or not_null_violation then
      raise exception 'invalid campaign link' using errcode='22023';
    end $$
    """,
    """
    create function app_private.archive_campaign_link(
      p_actor uuid,p_seller uuid,p_link uuid,p_request uuid
    ) returns boolean language plpgsql volatile security definer
    __SEARCH_PATH__ as $$
    declare arquivado boolean;
    begin
__GUARDA_ESCRITA__
      if p_request is null or p_link is null then
        raise exception 'campaign link context is not authorized' using errcode='42501';
      end if;
      update app_private.campaign_link link set status='archived',archived_at=clock_timestamp()
      where link.seller_organization_id=p_seller and link.id=p_link and link.status='active';
      arquivado := found;
      if arquivado then
        insert into public.audit_event(seller_organization_id,actor_identity_user_id,
          action,target_type,target_id,result,request_id,metadata)
        values(p_seller,p_actor,'growth.campaign_link_archived','campaign_link',p_link,
          'succeeded',p_request,'{}'::jsonb);
      end if;
      return arquivado;
    end $$
    """,
    """
    create function app_private.list_campaign_links(p_actor uuid,p_seller uuid)
    returns jsonb language plpgsql stable security definer
    __SEARCH_PATH__ as $$
    declare resultado jsonb;
    begin
__GUARDA_LEITURA__
      select coalesce(jsonb_agg(__JSON_DO_LINK__ order by link.created_at desc,link.id),'[]'::jsonb)
        into resultado
      from (select * from app_private.campaign_link candidate
            where candidate.seller_organization_id=p_seller
            order by candidate.created_at desc,candidate.id limit __MAXIMO_LISTADOS__) link;
      return jsonb_build_object('schema_version',1,'links',resultado);
    end $$
    """,
    # Vendas por campanha no período. Mesma regra de contagem da #562: a venda
    # é o primeiro purchase.approved da compra, e o estorno o primeiro
    # refunded ou cancelled, no dia de São Paulo.
    """
    create function app_private.read_campaign_sales(
      p_actor uuid,p_seller uuid,p_from date,p_to date
    ) returns jsonb language plpgsql stable security definer
    __SEARCH_PATH__ as $$
    declare resultado jsonb;
    begin
__GUARDA_LEITURA__
      if p_from is null or p_to is null or p_to < p_from or p_to - p_from >= __DIAS__ then
        raise exception 'invalid campaign report period' using errcode='22023';
      end if;
      with compras as (
        select event.external_purchase_id_sha256 compra,
          (min(event.occurred_at) filter (where event.event_type='purchase.approved')
            at time zone '__FUSO__')::date vendida_em,
          (min(event.occurred_at) filter (
            where event.event_type in ('purchase.refunded','subscription.cancelled'))
            at time zone '__FUSO__')::date estornada_em
        from public.webhook_event event
        where event.seller_organization_id=p_seller
        group by event.external_purchase_id_sha256
      ), atribuidas as (
        select attribution.link_id,attribution.utm_source,attribution.utm_medium,
          attribution.utm_campaign,
          (compras.vendida_em between p_from and p_to)::int aprovadas,
          (compras.estornada_em between p_from and p_to)::int estornadas
        from compras
        join app_private.checkout_attribution attribution
          on attribution.seller_organization_id=p_seller
         and attribution.purchase_sha256=compras.compra
        where compras.vendida_em between p_from and p_to
           or compras.estornada_em between p_from and p_to
      ), agregado as (
        select link_id,utm_source,utm_medium,utm_campaign,
          sum(aprovadas)::int aprovadas,sum(estornadas)::int estornadas
        from atribuidas group by link_id,utm_source,utm_medium,utm_campaign
      )
      select coalesce(jsonb_agg(jsonb_build_object(
          'link_id',agregado.link_id,'link_name',link.name,
          'utm_source',agregado.utm_source,'utm_medium',agregado.utm_medium,
          'utm_campaign',agregado.utm_campaign,'approved',agregado.aprovadas,
          'refunded',agregado.estornadas)
          order by agregado.aprovadas desc,agregado.utm_source nulls last,agregado.utm_campaign nulls last),
        '[]'::jsonb)
        into resultado
      from agregado
      left join app_private.campaign_link link
        on link.seller_organization_id=p_seller and link.id=agregado.link_id;
      return jsonb_build_object('schema_version',1,'from',p_from,'to',p_to,'rows',resultado);
    end $$
    """,
    # Chamada pelo BFF com a credencial de serviço, no início do checkout,
    # depois de conferir o aceite da versão atual do texto. Confere de novo a
    # janela de 7 dias e a loja do link. A primeira atribuição da compra fica.
    """
    create function app_private.record_checkout_attribution(
      p_seller uuid,p_purchase_sha256 text,p_link uuid,
      p_utm_source text,p_utm_medium text,p_utm_campaign text,p_utm_content text,
      p_utm_term text,p_visited_at timestamptz,p_consent_text_version text
    ) returns boolean language plpgsql volatile security definer
    __SEARCH_PATH__ as $$
    declare v_link uuid;
    begin
      if p_seller is null or p_purchase_sha256 is null or p_visited_at is null
         or p_consent_text_version is null
         or p_visited_at < clock_timestamp() - interval '__JANELA__ days'
         or p_visited_at > clock_timestamp() + interval '1 minute'
         or not exists (select 1 from public.seller_organization seller
                        where seller.id=p_seller and seller.status='active') then
        return false;
      end if;
      select link.id into v_link from app_private.campaign_link link
      where link.seller_organization_id=p_seller and link.id=p_link;
      insert into app_private.checkout_attribution(seller_organization_id,purchase_sha256,
        link_id,utm_source,utm_medium,utm_campaign,utm_content,utm_term,visited_at,
        consent_text_version)
      values(p_seller,p_purchase_sha256,v_link,p_utm_source,p_utm_medium,p_utm_campaign,
        p_utm_content,p_utm_term,p_visited_at,p_consent_text_version)
      on conflict (seller_organization_id,purchase_sha256) do nothing;
      return found;
    exception when check_violation then
      return false;
    end $$
    """,
    # Retenção (#563): apaga, em lote, a atribuição sem venda aprovada em 48 h
    # e qualquer uma com mais de 13 meses. Só uma réplica por vez: a outra
    # recebe `skipped` e não espera.
    """
    create function app_private.purge_checkout_attribution(p_limit integer)
    returns jsonb language plpgsql volatile security definer
    __SEARCH_PATH__ as $$
    declare sem_venda integer; expiradas integer; v_limite integer;
    begin
      if not pg_try_advisory_xact_lock(hashtextextended('appsell.attribution_retention', 0)) then
        return jsonb_build_object('skipped',true,'without_sale',0,'expired',0);
      end if;
      v_limite := least(greatest(coalesce(p_limit,0),1),__LOTE__);
      with alvo as (
        select attribution.seller_organization_id,attribution.purchase_sha256
        from app_private.checkout_attribution attribution
        where attribution.created_at < clock_timestamp() - interval '__HORAS__ hours'
          and not exists (
            select 1 from public.webhook_event event
            where event.seller_organization_id=attribution.seller_organization_id
              and event.external_purchase_id_sha256=attribution.purchase_sha256
              and event.event_type='purchase.approved')
        order by attribution.created_at limit v_limite
      )
      delete from app_private.checkout_attribution attribution using alvo
      where attribution.seller_organization_id=alvo.seller_organization_id
        and attribution.purchase_sha256=alvo.purchase_sha256;
      get diagnostics sem_venda = row_count;
      with alvo as (
        select attribution.seller_organization_id,attribution.purchase_sha256
        from app_private.checkout_attribution attribution
        where attribution.created_at < clock_timestamp() - interval '__MESES__ months'
        order by attribution.created_at limit v_limite
      )
      delete from app_private.checkout_attribution attribution using alvo
      where attribution.seller_organization_id=alvo.seller_organization_id
        and attribution.purchase_sha256=alvo.purchase_sha256;
      get diagnostics expiradas = row_count;
      return jsonb_build_object('skipped',false,'without_sale',sem_venda,'expired',expiradas);
    end $$
    """,
    # Contagem do aceite ou da recusa, sem dado pessoal. Chamada pelo BFF com a
    # credencial de serviço, com limite de taxa por IP lá.
    """
    create function app_private.record_cookie_consent(
      p_seller uuid,p_text_version text,p_accepted boolean
    ) returns boolean language plpgsql volatile security definer
    __SEARCH_PATH__ as $$
    begin
      if p_seller is null or p_text_version is null or p_accepted is null
         or not exists (select 1 from public.seller_organization seller
                        where seller.id=p_seller and seller.status='active') then
        return false;
      end if;
      insert into app_private.cookie_consent_tally(seller_organization_id,day,text_version,
        accepted,refused)
      values(p_seller,(clock_timestamp() at time zone '__FUSO__')::date,p_text_version,
        p_accepted::int,(not p_accepted)::int)
      on conflict (seller_organization_id,day,text_version) do update
        set accepted=app_private.cookie_consent_tally.accepted+excluded.accepted,
            refused=app_private.cookie_consent_tally.refused+excluded.refused;
      return true;
    exception when check_violation then
      return false;
    end $$
    """,
)


def _guarda(capacidade: str) -> str:
    return _GUARDA.replace("__CAPACIDADE__", capacidade)


_VALORES = {
    "__COLUNAS_UTM__": _COLUNAS_UTM,
    "__CHECKS_UTM__": _checks_utm(),
    "__NOME__": str(TAMANHO_NOME),
    "__GUARDA_ESCRITA__": _guarda(CAPACIDADE_ESCRITA),
    "__GUARDA_LEITURA__": _guarda(CAPACIDADE_LEITURA),
    "__JSON_DO_LINK__": _JSON_DO_LINK,
    "__SEARCH_PATH__": SEARCH_PATH,
    "__MAXIMO_ATIVOS__": str(MAXIMO_DE_LINKS_ATIVOS),
    "__MAXIMO_LISTADOS__": str(MAXIMO_DE_LINKS_LISTADOS),
    "__DIAS__": str(PERIODO_MAXIMO_DIAS),
    "__JANELA__": str(JANELA_DIAS),
    "__FUSO__": FUSO,
    "__HORAS__": str(HORAS_SEM_VENDA),
    "__MESES__": str(MESES_DE_RETENCAO),
    "__LOTE__": str(LOTE_MAXIMO_DA_LIMPEZA),
}


# Molde sem f-string, como na 0049: os valores entram por replace, e o ruff não
# vê SQL montado por interpolação (S608) onde só há constantes deste arquivo.
def _preencher(molde: str) -> str:
    for marcador, valor in _VALORES.items():
        molde = molde.replace(marcador, valor)
    return molde


CRIA_TABELAS = tuple(_preencher(molde) for molde in _CRIA_TABELAS_MOLDE)
FUNCOES = tuple(_preencher(molde) for molde in _FUNCOES_MOLDE)

ASSINATURAS = (
    "app_private.create_campaign_link(uuid,uuid,text,text,text,text,text,text,text,uuid)",
    "app_private.archive_campaign_link(uuid,uuid,uuid,uuid)",
    "app_private.list_campaign_links(uuid,uuid)",
    "app_private.read_campaign_sales(uuid,uuid,date,date)",
    "app_private.record_checkout_attribution("
    "uuid,text,uuid,text,text,text,text,text,timestamptz,text)",
    "app_private.record_cookie_consent(uuid,text,boolean)",
    "app_private.purge_checkout_attribution(integer)",
)


def _reescreve(alvo: str, de: str, para: str) -> str:
    return _REESCREVE.replace("ALVO", alvo).replace("DE", de).replace("PARA", para)


def upgrade() -> None:
    for sql in CRIA_TABELAS:
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


def downgrade() -> None:
    # Apaga links, atribuições e contagens (docstring).
    for alvo in _BASES_DO_READY:
        op.execute(_reescreve(alvo, READY_DEPOIS, READY_ANTES))
    for assinatura in reversed(ASSINATURAS):
        op.execute(f"drop function if exists {assinatura}")
    for tabela in reversed(TABELAS):
        op.execute(f"drop table if exists app_private.{tabela}")
