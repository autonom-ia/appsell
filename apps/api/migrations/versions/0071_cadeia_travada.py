"""O alerta de cadeia travada lê o lado do e-mail e o resumo da semana (#550).

O volume atual de vendas não sustenta alerta de frescor por horário. O que
importa é a venda que entrou e não chegou ao cliente. A metade do fulfillment
já existe: `checkout_reconciliation_report` (0063) mostra o job em
`dead_letter`, o job parado, a venda sem job, o job sem grant e o grant sem
acesso. Esta migration cria a outra metade, **uma** leitura:
`app_private.chain_health_report`.

Ela só devolve métrica, quantidade e instante, nunca linha de cliente, e-mail
ou identificador de pessoa. É `stable`, `security definer` e não é concedida
ao `appsell_runtime`. Quem chama é o checador do host
(`scripts/ops/cadeia_travada.py`), com o papel `appsell_reconciler`, o mesmo da
conciliação: `connect`, `usage` em `app_private` e `execute` nas duas leituras,
mais nada. O papel nasce fora da migration, porque o migrator é
`nocreaterole`. Por isso o grant só acontece se ele existir.

| Métrica | O que conta |
|---|---|
| `email_stalled` | e-mail em `pending`, `retry` ou `leased` há mais que `p_email_stale_after` |
| `email_dead_letter` | e-mail que foi a `dead_letter` desde `p_since`, menos o descadastro (`suppressed`, `no_consent`) |
| `grant_without_email` | grant ativo criado em `[p_since - p_grant_stale_after, now() - p_grant_stale_after)` cujo e-mail da compra não está `sent` |
| `week_sales` | `purchase.approved` recebidos nos últimos 7 dias |
| `week_grants` | grants criados nos últimos 7 dias |
| `week_emails_sent` | e-mails enviados nos últimos 7 dias |

Em toda métrica de problema, `observed_at` é o instante do caso **mais novo**.
Com ele, o checador vê um caso novo mesmo quando a contagem não muda (um sai,
outro entra).

`grant_without_email` usa janela de propósito. A linha de e-mail nasce na
mesma transação do grant desde a 0049, e um grant de antes dela não tem e-mail
nenhum. Sem janela, todo grant antigo apareceria para sempre. A janela anda
deslocada pelo limite: o checador manda `p_since` = fim da rodada anterior, e
cada grant é olhado quando passa dos `p_grant_stale_after`. Sem o
deslocamento, o intervalo `[p_since, now() - limite)` fica vazio no ritmo de
5 minutos.

`email_dead_letter` também é janela: o `dead_letter` é final e alerta uma vez,
quando acontece. O descadastro (0066) vai a `dead_letter` por decisão da
pessoa, e não é cadeia travada.

O `downgrade` só remove a leitura: não há dado a preservar.

Revision ID: 0071_cadeia_travada
Revises: 0065_dono_da_conta_payout
"""

from __future__ import annotations

from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0071_cadeia_travada"
down_revision = "0065_dono_da_conta_payout"
branch_labels = None
depends_on = None

_READY_ATE_0065 = (
    "'0057_banner_na_vitrine','0058_capa_na_biblioteca','0059_canal_financial_global',"
    "'0060_atualizacao_chega_ao_aluno','0061_o_atraso_alcanca_o_aluno',"
    "'0062_mapping_do_produto_novo','0063_conciliacao_do_acesso',"
    "'0064_vendas_ativadas_pela_loja','0065_dono_da_conta_payout'))"
)
_READY_ATE_0071 = (
    "'0057_banner_na_vitrine','0058_capa_na_biblioteca','0059_canal_financial_global',"
    "'0060_atualizacao_chega_ao_aluno','0061_o_atraso_alcanca_o_aluno',"
    "'0062_mapping_do_produto_novo','0063_conciliacao_do_acesso',"
    "'0064_vendas_ativadas_pela_loja','0065_dono_da_conta_payout',"
    "'0071_cadeia_travada'))"
)
_BASES_DO_READY: tuple[str, ...] = (
    "app_private.schema_ready_base_0016()",
    "app_private.schema_ready_base_0017()",
)
_BASE_DO_ACESSO = "app_private.schema_ready_base_0016()"
_LEITURA = "app_private.chain_health_report(timestamptz,interval,interval)"
# Literal de propósito: o papel e a assinatura não vêm de entrada nenhuma.
_CONCEDE = """
do $migration$
begin
  if exists(select 1 from pg_roles where rolname='appsell_reconciler') then
    grant execute on function
      app_private.chain_health_report(timestamptz,interval,interval)
      to appsell_reconciler;
  end if;
end $migration$
"""
_RETIRA = """
do $migration$
begin
  if exists(select 1 from pg_roles where rolname='appsell_reconciler') then
    revoke execute on function
      app_private.chain_health_report(timestamptz,interval,interval)
      from appsell_reconciler;
  end if;
end $migration$
"""

_FUNCAO = r"""
create function app_private.chain_health_report(
  p_since timestamptz, p_email_stale_after interval, p_grant_stale_after interval
) returns table(metric text, quantity bigint, observed_at timestamptz)
language plpgsql stable security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
#variable_conflict use_column
begin
  if p_since is null or p_email_stale_after is null or p_grant_stale_after is null
     or p_since < now() - interval '31 days' or p_since > now()
     or p_email_stale_after < interval '1 minute' or p_email_stale_after > interval '1 day'
     or p_grant_stale_after < interval '1 minute' or p_grant_stale_after > interval '1 day' then
    raise exception 'chain health window is invalid' using errcode='22023';
  end if;

  return query
  select 'email_stalled'::text, count(*), max(outbox.created_at)
  from app_private.email_outbox outbox
  where outbox.status in ('pending','retry','leased')
    and outbox.created_at < now() - p_email_stale_after;

  return query
  select 'email_dead_letter'::text, count(*), max(outbox.updated_at)
  from app_private.email_outbox outbox
  where outbox.status='dead_letter'
    and outbox.updated_at >= p_since
    and outbox.error_code is distinct from 'suppressed'
    and outbox.error_code is distinct from 'no_consent';

  return query
  select 'grant_without_email'::text, count(*), max(grant_row.created_at)
  from app_private.entitlement_grant grant_row
  where grant_row.status='active'
    and grant_row.created_at >= p_since - p_grant_stale_after
    and grant_row.created_at < now() - p_grant_stale_after
    and not exists (
      select 1 from app_private.email_outbox outbox
      where outbox.seller_organization_id=grant_row.seller_organization_id
        and outbox.dedupe_key='purchase:'||grant_row.checkout_integration_id::text
          ||':'||grant_row.external_purchase_id_sha256
        and outbox.status='sent');

  return query
  select 'week_sales'::text, count(*), max(event.received_at)
  from public.webhook_event event
  where event.event_type='purchase.approved'
    and event.received_at >= now() - interval '7 days';

  return query
  select 'week_grants'::text, count(*), max(grant_row.created_at)
  from app_private.entitlement_grant grant_row
  where grant_row.created_at >= now() - interval '7 days';

  return query
  select 'week_emails_sent'::text, count(*), max(outbox.sent_at)
  from app_private.email_outbox outbox
  where outbox.status='sent' and outbox.sent_at >= now() - interval '7 days';
end $$
"""

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


def _conciliacao() -> ModuleType:
    """Lê a âncora de acesso que a `0063` deixou no `schema_ready`."""
    caminho = Path(__file__).with_name("0063_conciliacao_do_acesso.py")
    spec = importlib_util.spec_from_file_location("appsell_migration_0063", caminho)
    if spec is None or spec.loader is None:  # pragma: no cover - caminho fixo no repo
        raise RuntimeError("a migration 0063 nao pode ser lida")
    modulo = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _ready_funcao() -> tuple[str, str]:
    de = _conciliacao()._ready_funcao()[1]
    return de, de + f"            and to_regprocedure('{_LEITURA}') is not null\n"


def upgrade() -> None:
    op.execute("set local role appsell_owner")
    op.execute(_FUNCAO)
    op.execute(f"revoke all on function {_LEITURA} from public")
    op.execute(_CONCEDE)
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0065)
            .replace("PARA", _READY_ATE_0071)
        )
    de, para = _ready_funcao()
    op.execute(_REESCREVE.replace("ALVO", _BASE_DO_ACESSO).replace("DE", de).replace("PARA", para))


def downgrade() -> None:
    """Só remove uma leitura: não há dado a preservar."""
    op.execute("set local role appsell_owner")
    de, para = _ready_funcao()
    op.execute(_REESCREVE.replace("ALVO", _BASE_DO_ACESSO).replace("DE", para).replace("PARA", de))
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0071)
            .replace("PARA", _READY_ATE_0065)
        )
    op.execute(_RETIRA)
    op.execute(f"drop function {_LEITURA}")
