"""O destino do pagamento diz também se a conta já repassa (#545).

A loja que cobra na própria conta conectada (#440) só pode vender com a conta
**verificada**: `charges_enabled` **e** `payouts_enabled`. A função da 0024,
`app_private.resolve_payout_destination(uuid,text)`, devolve só
`charges_enabled`. O tipo de retorno de uma função não muda com `create or
replace`, e a 0024 já está aplicada, então esta migration cria uma função nova
ao lado, com a mesma trava de capacidade:

* `app_private.resolve_payout_destination_v2(uuid,text)` devolve
  `provider_account_id`, `charges_enabled` e `payouts_enabled`.

Os flags vêm de `public.seller_payout_account`, que o `account.updated` do
endpoint Connect do Sell mantém (0023). A conta que perde a verificação depois
de ativa volta a ser recusada na próxima venda, sem passo manual.

A função da 0024 fica: a api passa a chamar a v2, mas a imagem anterior,
durante o deploy, ainda chama a antiga.

Downgrade: tira a v2 e a versão do readiness.

Revision ID: 0073_destino_com_repasse
Revises: 0066_consentimento_de_email
"""

from __future__ import annotations

from alembic import op

revision = "0073_destino_com_repasse"
down_revision = "0066_consentimento_de_email"
branch_labels = None
depends_on = None

DESTINO_V2 = "app_private.resolve_payout_destination_v2(uuid,text)"

_READY_ATE_0066 = (
    "'0057_banner_na_vitrine','0058_capa_na_biblioteca','0059_canal_financial_global',"
    "'0060_atualizacao_chega_ao_aluno','0061_o_atraso_alcanca_o_aluno',"
    "'0062_mapping_do_produto_novo','0063_conciliacao_do_acesso',"
    "'0064_vendas_ativadas_pela_loja','0065_dono_da_conta_payout',"
    "'0066_consentimento_de_email'))"
)
_READY_ATE_0073 = (
    "'0057_banner_na_vitrine','0058_capa_na_biblioteca','0059_canal_financial_global',"
    "'0060_atualizacao_chega_ao_aluno','0061_o_atraso_alcanca_o_aluno',"
    "'0062_mapping_do_produto_novo','0063_conciliacao_do_acesso',"
    "'0064_vendas_ativadas_pela_loja','0065_dono_da_conta_payout',"
    "'0066_consentimento_de_email','0073_destino_com_repasse'))"
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

# Mesma trava da 0024: só o worker do checkout, sem loja no envelope.
FUNCAO_DESTINO_V2 = """
create function app_private.resolve_payout_destination_v2(
  p_seller_organization_id uuid, p_provider text
)
returns table(provider_account_id text, charges_enabled boolean, payouts_enabled boolean)
language plpgsql stable security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
begin
  if app_private.context_text('capability')
       is distinct from 'commerce.checkout.destination'
    or app_private.context_uuid('seller_organization_id') is not null
    or p_seller_organization_id is null
  then
    raise exception 'payout destination read denied' using errcode = '42501';
  end if;
  return query
    select account.provider_account_id, account.charges_enabled, account.payouts_enabled
    from public.seller_payout_account account
    where account.seller_organization_id = p_seller_organization_id
      and account.provider = p_provider;
end $$
"""


def upgrade() -> None:
    op.execute("set local role appsell_owner")
    op.execute(FUNCAO_DESTINO_V2)
    op.execute(f"revoke all on function {DESTINO_V2} from public")
    op.execute(f"grant execute on function {DESTINO_V2} to appsell_runtime")
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0066)
            .replace("PARA", _READY_ATE_0073)
        )


def downgrade() -> None:
    op.execute("set local role appsell_owner")
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0073)
            .replace("PARA", _READY_ATE_0066)
        )
    op.execute(f"drop function {DESTINO_V2}")
