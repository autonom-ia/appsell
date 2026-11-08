"""Base de leads da loja: quem comprou ou recebeu acesso, e o export de marketing (#619).

Sem tabela nova. A lista junta o que já existe:
- `buyer_entitlement`: quem tem ou teve acesso, a qual produto, desde quando e
  por qual caminho;
- `identity_user`: o e-mail e o nome;
- `catalog_product`: o título do produto;
- o consentimento e as supressões de e-mail da 0066.

Esta migration cria só duas leituras, presas à loja do contexto e ao papel de
dono ou admin:
- `read_store_leads`: a tela, com as primeiras 500 linhas e os totais;
- `export_store_leads`: o CSV "para marketing", **uma linha por pessoa**, com
  os produtos juntos numa coluna. Leva **só** quem pode receber e-mail
  opcional da loja agora, decidido pela própria
  `app_private.email_optional_block_reason` (finalidade `store_news`). Assim, a
  lista e o envio nunca discordam. Grava `audit_event` sem e-mail, tem limite de
  5 exports por minuto por pessoa e uma trava por pessoa antes da contagem, como
  o export de vendas (0072).

Não aparecem:
- as entradas de demo (`demo_seed`, `demo_checkout`);
- quem tem pedido de exclusão de conta em aberto (0051): quem pediu para sair
  não entra numa lista exportável enquanto espera.

O e-mail sai inteiro para o dono ou o admin, que é o controlador desse dado
(Política de Privacidade, seção 2), e nunca vai ao log.

O `downgrade` só remove as duas funções: não há dado a preservar.

Revision ID: 0078_base_de_leads
Revises: 0072_relatorio_de_vendas
"""

from __future__ import annotations

from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0078_base_de_leads"
down_revision = "0072_relatorio_de_vendas"
branch_labels = None
depends_on = None

CAPACIDADE_LEITURA = "growth.leads.read"
CAPACIDADE_EXPORT = "growth.leads.export"
PERIODO_MAXIMO_DIAS = 366
EXPORTS_POR_MINUTO = 5
LINHAS_NA_TELA = 500
MAXIMO_DE_LINHAS = 10000
FUSO = "America/Sao_Paulo"
FINALIDADE = "store_news"
ORIGENS = ("billing", "free_access", "manual")


def _carregar(revisao: str) -> ModuleType:
    caminho = Path(__file__).with_name(f"{revisao}.py")
    spec = importlib_util.spec_from_file_location(f"appsell_anterior_{revisao}", caminho)
    if spec is None or spec.loader is None:  # pragma: no cover - caminho fixo no repo
        raise RuntimeError(f"a migration {revisao} nao pode ser lida")
    modulo = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


# A lista do readiness vem da anterior na cadeia, não de uma cópia escrita aqui.
READY_ANTES: str = _carregar(down_revision)._READY_ATE_0072
READY_DEPOIS = READY_ANTES[:-2] + f",'{revision}'))"
# Nome que as migrations seguintes leem por `getattr` (padrão da 0067 em diante).
_READY_ATE_0078 = READY_DEPOIS
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

# Guarda comum às duas funções, como na 0072.
_GUARDA = """
      if p_actor is null or p_seller is null
         or app_private.context_text('capability') is distinct from '__CAPACIDADE__'
         or app_private.context_uuid('identity_user_id') is distinct from p_actor
         or app_private.context_uuid('seller_organization_id') is distinct from p_seller
         or coalesce(app_private.resolve_seller_membership_role(p_seller),'')
            not in ('seller_owner','seller_admin') then
        raise exception 'store leads context is not authorized' using errcode='42501';
      end if;
      if p_from is null or p_to is null or p_to < p_from or p_to - p_from >= __DIAS__ then
        raise exception 'invalid store leads period' using errcode='22023';
      end if;
"""

# Uma linha por acesso (pessoa e produto) da loja no período, com a situação do
# e-mail de marketing. A ordem dos casos importa: a supressão global vence tudo,
# o descadastro da loja vence o consentimento, e "consentiu" é exatamente o que
# libera o envio (`email_optional_block_reason` nulo).
_LEADS = """
      with acessos as (
        select ent.id acesso,ent.buyer_identity_user_id pessoa,ent.catalog_product_id produto,
          ent.source origem,ent.status situacao_do_acesso,ent.created_at entrou_em,
          app_private.normalize_email(person.email_normalized) email,
          person.display_name nome,catalog.title produto_titulo
        from public.buyer_entitlement ent
        join public.identity_user person on person.id=ent.buyer_identity_user_id
        join public.catalog_product catalog
          on catalog.seller_organization_id=ent.seller_organization_id
         and catalog.id=ent.catalog_product_id
        where ent.seller_organization_id=p_seller
          and ent.source in (__ORIGENS__)
          and (p_product is null or ent.catalog_product_id=p_product)
          and (ent.created_at at time zone '__FUSO__')::date between p_from and p_to
          and not exists(
            select 1 from public.audit_event pedido
            where pedido.actor_identity_user_id=ent.buyer_identity_user_id
              and pedido.action='account.deletion.requested'
              and pedido.result='succeeded'
              and not exists(
                select 1 from public.audit_event feito
                where feito.action='account.deletion.completed'
                  and feito.result='succeeded'
                  and feito.target_type='identity_user'
                  and feito.target_id=ent.buyer_identity_user_id
                  and feito.created_at>pedido.created_at))
      ), leads as (
        select acessos.*,case
          when exists(select 1 from app_private.email_suppression_global global
                      where global.email_sha256=app_private.email_sha256(acessos.email))
            then 'suppressed'
          when exists(select 1 from app_private.email_suppression scoped
                      where scoped.scope_id=p_seller
                        and scoped.email_sha256=app_private.email_sha256(acessos.email))
            or exists(select 1 from app_private.email_consent consent
                      where consent.scope_id=p_seller and consent.email_normalized=acessos.email
                        and consent.purpose='__FINALIDADE__' and consent.status='revoked')
            then 'unsubscribed'
          when app_private.email_optional_block_reason(p_seller,acessos.email,'__FINALIDADE__')
               is null
            then 'granted'
          else 'none' end situacao_do_email
        from acessos
      )
"""

_LINHA = """jsonb_build_object('entitlement_id',linha.acesso,'name',linha.nome,
          'email',linha.email,'catalog_product_id',linha.produto,
          'product_title',linha.produto_titulo,'source',linha.origem,
          'access_status',linha.situacao_do_acesso,
          'since',(linha.entrou_em at time zone '__FUSO__')::date,
          'email_status',linha.situacao_do_email)"""

_LEITURA_MOLDE = """
    create function app_private.read_store_leads(
      p_actor uuid,p_seller uuid,p_from date,p_to date,p_product uuid
    ) returns jsonb language plpgsql stable security definer
    set search_path=pg_catalog,app_private,public,pg_temp as $$
    declare resultado jsonb;
    begin
__GUARDA__
__LEADS__
      , primeiras as (
        select * from leads order by entrou_em desc,acesso limit __NA_TELA__
      )
      select jsonb_build_object('schema_version',1,'from',p_from,'to',p_to,
        'total_rows',(select count(*) from leads),
        'total_people',(select count(distinct pessoa) from leads),
        'marketing_people',(select count(distinct pessoa) from leads
                            where situacao_do_email='granted'),
        'rows',coalesce((select jsonb_agg(__LINHA__ order by linha.entrou_em desc,linha.acesso)
                         from primeiras linha),'[]'::jsonb))
        into resultado;
      return resultado;
    end $$
    """

_EXPORT_MOLDE = """
    create function app_private.export_store_leads(
      p_actor uuid,p_seller uuid,p_from date,p_to date,p_product uuid,p_request uuid
    ) returns jsonb language plpgsql volatile security definer
    set search_path=pg_catalog,app_private,public,pg_temp as $$
    declare resultado jsonb; total integer;
    begin
__GUARDA__
      if p_request is null then
        raise exception 'store leads context is not authorized' using errcode='42501';
      end if;
      -- Trava por pessoa até o fim da transação: dois exports ao mesmo tempo
      -- não leem a mesma contagem antes de um deles gravar a auditoria.
      perform pg_advisory_xact_lock(hashtextextended('appsell.leads_export:' || p_actor::text, 0));
      if (select count(*) from public.audit_event audit
          where audit.actor_identity_user_id=p_actor
            and audit.action='growth.leads_exported'
            and audit.created_at>clock_timestamp()-interval '1 minute') >= __POR_MINUTO__ then
        raise exception 'store leads export rate limited' using errcode='P4291';
      end if;
__LEADS__
      -- Uma linha por pessoa: o mesmo e-mail nunca sai duas vezes, e quem
      -- importa numa ferramenta de e-mail não manda em dobro. Os produtos vão
      -- juntos numa coluna, e "desde" é a primeira entrada no período.
      , marketing as (
        select pessoa,min(nome) nome,email,
          string_agg(distinct produto_titulo,'; ' order by produto_titulo) produtos,
          min(entrou_em) entrou_em
        from leads where situacao_do_email='granted'
        group by pessoa,email
      )
      select count(*),coalesce(jsonb_agg(jsonb_build_object('name',linha.nome,
          'email',linha.email,'products',linha.produtos,
          'since',(linha.entrou_em at time zone '__FUSO__')::date)
          order by linha.entrou_em desc,linha.email),'[]'::jsonb)
        into total,resultado from marketing linha;
      if total > __MAXIMO__ then
        raise exception 'store leads export too large' using errcode='P4131';
      end if;
      insert into public.audit_event(seller_organization_id,actor_identity_user_id,
        action,target_type,target_id,result,request_id,metadata)
      values(p_seller,p_actor,'growth.leads_exported','store_leads',null,'succeeded',
        p_request,jsonb_build_object('from',p_from,'to',p_to,'product',p_product,
          'audience','marketing','rows',total));
      return jsonb_build_object('schema_version',1,'rows',resultado);
    end $$
    """


def _preencher(molde: str, capacidade: str) -> str:
    origens = ",".join(f"'{origem}'" for origem in ORIGENS)
    return (
        molde.replace("__GUARDA__", _GUARDA)
        .replace("__LEADS__", _LEADS)
        .replace("__LINHA__", _LINHA)
        .replace("__CAPACIDADE__", capacidade)
        .replace("__ORIGENS__", origens)
        .replace("__FINALIDADE__", FINALIDADE)
        .replace("__DIAS__", str(PERIODO_MAXIMO_DIAS))
        .replace("__FUSO__", FUSO)
        .replace("__NA_TELA__", str(LINHAS_NA_TELA))
        .replace("__POR_MINUTO__", str(EXPORTS_POR_MINUTO))
        .replace("__MAXIMO__", str(MAXIMO_DE_LINHAS))
    )


FUNCOES = (
    _preencher(_LEITURA_MOLDE, CAPACIDADE_LEITURA),
    _preencher(_EXPORT_MOLDE, CAPACIDADE_EXPORT),
)
_ASSINATURAS = (
    "app_private.read_store_leads(uuid,uuid,date,date,uuid)",
    "app_private.export_store_leads(uuid,uuid,date,date,uuid,uuid)",
)


def _reescreve(alvo: str, de: str, para: str) -> str:
    return _REESCREVE.replace("ALVO", alvo).replace("DE", de).replace("PARA", para)


def upgrade() -> None:
    for sql in FUNCOES:
        op.execute(sql)
    for assinatura in _ASSINATURAS:
        op.execute(f"alter function {assinatura} owner to appsell_owner")
        op.execute(f"revoke all on function {assinatura} from public")
        op.execute(f"grant execute on function {assinatura} to appsell_runtime")
    for alvo in _BASES_DO_READY:
        op.execute(_reescreve(alvo, READY_ANTES, READY_DEPOIS))


def downgrade() -> None:
    for alvo in _BASES_DO_READY:
        op.execute(_reescreve(alvo, READY_DEPOIS, READY_ANTES))
    for assinatura in reversed(_ASSINATURAS):
        op.execute(f"drop function if exists {assinatura}")
