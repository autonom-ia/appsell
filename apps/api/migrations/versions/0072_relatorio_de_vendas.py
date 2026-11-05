"""Relatório de vendas por período e produto, e as linhas do CSV (#562 v1).

As vendas vivem no Financial. O que chega ao Sell são os eventos assinados,
gravados em `webhook_event`, imutáveis e idempotentes por
`(seller, endpoint, sha do event_id)`. Essa tabela já é a cópia das vendas.
Esta migration não cria tabela: cria duas leituras sobre ela, presas à loja
do contexto e ao papel de dono ou admin.

Regra de contagem, por compra `(integração, sha da compra)`:
- **venda**: o primeiro `purchase.approved` da compra. Replay do mesmo evento
  não cria linha, e reaprovação da mesma compra não conta segunda venda;
- **estorno**: o primeiro `purchase.refunded` ou `subscription.cancelled`;
- o dia é o de São Paulo (a loja vende em BRL), e o período é inclusivo nas
  duas pontas, até 366 dias.

O valor em R$ fica para a v2: o webhook do Financial não traz valor
(combinado com a trilha Dinheiro na #562).

Nada de dado pessoal sai daqui: nem e-mail, nem identidade, nem payload. A
compra aparece no CSV pelos 12 primeiros caracteres do sha que o próprio
ingress guarda. O export grava `audit_event` e tem limite de 5 por minuto por
pessoa, contado no banco, para valer em todas as réplicas.

O `downgrade` só remove as duas funções: não há dado a preservar.

Revision ID: 0072_relatorio_de_vendas
Revises: 0065_dono_da_conta_payout
"""

from __future__ import annotations

from alembic import op

revision = "0072_relatorio_de_vendas"
down_revision = "0065_dono_da_conta_payout"
branch_labels = None
depends_on = None

CAPACIDADE_LEITURA = "commerce.sales.report.read"
CAPACIDADE_EXPORT = "commerce.sales.report.export"
PERIODO_MAXIMO_DIAS = 366
EXPORTS_POR_MINUTO = 5
MAXIMO_DE_LINHAS = 10000
FUSO = "America/Sao_Paulo"

READY_ANTES = (
    "'0057_banner_na_vitrine','0058_capa_na_biblioteca','0059_canal_financial_global',"
    "'0060_atualizacao_chega_ao_aluno','0061_o_atraso_alcanca_o_aluno',"
    "'0062_mapping_do_produto_novo','0063_conciliacao_do_acesso',"
    "'0064_vendas_ativadas_pela_loja','0065_dono_da_conta_payout'))"
)
READY_DEPOIS = (
    "'0057_banner_na_vitrine','0058_capa_na_biblioteca','0059_canal_financial_global',"
    "'0060_atualizacao_chega_ao_aluno','0061_o_atraso_alcanca_o_aluno',"
    "'0062_mapping_do_produto_novo','0063_conciliacao_do_acesso',"
    "'0064_vendas_ativadas_pela_loja','0065_dono_da_conta_payout',"
    "'0072_relatorio_de_vendas'))"
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

# Guarda comum às duas funções. A capacidade entra por replace (molde, como na
# 0049), para o ruff não ver SQL montado por interpolação.
_GUARDA = """
      if p_actor is null or p_seller is null
         or app_private.context_text('capability') is distinct from '__CAPACIDADE__'
         or app_private.context_uuid('identity_user_id') is distinct from p_actor
         or app_private.context_uuid('seller_organization_id') is distinct from p_seller
         or coalesce(app_private.resolve_seller_membership_role(p_seller),'')
            not in ('seller_owner','seller_admin') then
        raise exception 'sales report context is not authorized' using errcode='42501';
      end if;
      if p_from is null or p_to is null or p_to < p_from or p_to - p_from > __DIAS__ then
        raise exception 'invalid sales report period' using errcode='22023';
      end if;
"""

# Uma linha por compra da loja, com o produto do catálogo e as duas datas.
_COMPRAS = """
      with eventos as (
        select event.checkout_integration_id integracao,
          event.external_purchase_id_sha256 compra,event.event_type tipo,event.occurred_at,
          mapping.catalog_product_id produto
        from public.webhook_event event
        join public.external_product_mapping mapping
          on mapping.seller_organization_id=event.seller_organization_id
         and mapping.checkout_integration_id=event.checkout_integration_id
         and mapping.external_product_id_sha256=event.external_product_id_sha256
        where event.seller_organization_id=p_seller
          and (p_product is null or mapping.catalog_product_id=p_product)
      ), compras as (
        select integracao,compra,produto,
          (min(occurred_at) filter (where tipo='purchase.approved')
            at time zone '__FUSO__')::date vendida_em,
          (min(occurred_at) filter (
            where tipo in ('purchase.refunded','subscription.cancelled'))
            at time zone '__FUSO__')::date estornada_em
        from eventos group by integracao,compra,produto
      )
"""

_LEITURA_MOLDE = """
    create function app_private.read_sales_report(
      p_actor uuid,p_seller uuid,p_from date,p_to date,p_product uuid
    ) returns jsonb language plpgsql stable security definer
    set search_path=pg_catalog,app_private,public as $$
    declare resultado jsonb;
    begin
__GUARDA__
__COMPRAS__
      , linhas as (
        select produto,vendida_em dia,1 aprovadas,0 estornadas from compras
          where vendida_em between p_from and p_to
        union all
        select produto,estornada_em,0,1 from compras
          where estornada_em between p_from and p_to
      ), agregado as (
        select produto,dia,sum(aprovadas)::int aprovadas,sum(estornadas)::int estornadas
        from linhas group by produto,dia
      )
      select jsonb_build_object('schema_version',1,'from',p_from,'to',p_to,
        'rows',coalesce(jsonb_agg(jsonb_build_object(
          'day',agregado.dia,'catalog_product_id',agregado.produto,
          'product_title',catalog.title,'approved',agregado.aprovadas,
          'refunded',agregado.estornadas) order by agregado.dia,catalog.title),'[]'::jsonb))
        into resultado
      from agregado
      join public.catalog_product catalog
        on catalog.seller_organization_id=p_seller and catalog.id=agregado.produto;
      return resultado;
    end $$
    """

_EXPORT_MOLDE = """
    create function app_private.export_sales_rows(
      p_actor uuid,p_seller uuid,p_from date,p_to date,p_product uuid,p_request uuid
    ) returns jsonb language plpgsql volatile security definer
    set search_path=pg_catalog,app_private,public as $$
    declare resultado jsonb; total integer;
    begin
__GUARDA__
      if p_request is null then
        raise exception 'sales report context is not authorized' using errcode='42501';
      end if;
      if (select count(*) from public.audit_event audit
          where audit.actor_identity_user_id=p_actor
            and audit.action='commerce.sales_exported'
            and audit.created_at>clock_timestamp()-interval '1 minute') >= __POR_MINUTO__ then
        raise exception 'sales export rate limited' using errcode='P4291';
      end if;
__COMPRAS__
      , escolhidas as (
        select compras.*,catalog.title from compras
        join public.catalog_product catalog
          on catalog.seller_organization_id=p_seller and catalog.id=compras.produto
        where vendida_em between p_from and p_to or estornada_em between p_from and p_to
      )
      select count(*),coalesce(jsonb_agg(jsonb_build_object(
          'sold_on',vendida_em,'refunded_on',estornada_em,'product_title',title,
          'status',case when estornada_em is null then 'approved' else 'refunded' end,
          'purchase_ref',left(compra,12)) order by vendida_em nulls last,title,compra),'[]'::jsonb)
        into total,resultado from escolhidas;
      if total > __MAXIMO__ then
        raise exception 'sales export too large' using errcode='P4131';
      end if;
      insert into public.audit_event(seller_organization_id,actor_identity_user_id,
        action,target_type,target_id,result,request_id,metadata)
      values(p_seller,p_actor,'commerce.sales_exported','sales_report',null,'succeeded',
        p_request,jsonb_build_object('from',p_from,'to',p_to,'product',p_product,'rows',total));
      return jsonb_build_object('schema_version',1,'rows',resultado);
    end $$
    """


def _preencher(molde: str, capacidade: str) -> str:
    return (
        molde.replace("__GUARDA__", _GUARDA)
        .replace("__COMPRAS__", _COMPRAS)
        .replace("__CAPACIDADE__", capacidade)
        .replace("__DIAS__", str(PERIODO_MAXIMO_DIAS))
        .replace("__FUSO__", FUSO)
        .replace("__POR_MINUTO__", str(EXPORTS_POR_MINUTO))
        .replace("__MAXIMO__", str(MAXIMO_DE_LINHAS))
    )


FUNCOES = (
    _preencher(_LEITURA_MOLDE, CAPACIDADE_LEITURA),
    _preencher(_EXPORT_MOLDE, CAPACIDADE_EXPORT),
)
_ASSINATURAS = (
    "app_private.read_sales_report(uuid,uuid,date,date,uuid)",
    "app_private.export_sales_rows(uuid,uuid,date,date,uuid,uuid)",
)


def upgrade() -> None:
    for sql in FUNCOES:
        op.execute(sql)
    for assinatura in _ASSINATURAS:
        op.execute(f"alter function {assinatura} owner to appsell_owner")
        op.execute(f"revoke all on function {assinatura} from public")
        op.execute(f"grant execute on function {assinatura} to appsell_runtime")
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", READY_ANTES)
            .replace("PARA", READY_DEPOIS)
        )


def downgrade() -> None:
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", READY_DEPOIS)
            .replace("PARA", READY_ANTES)
        )
    for assinatura in reversed(_ASSINATURAS):
        op.execute(f"drop function if exists {assinatura}")
