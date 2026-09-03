"""As seis funções de recebimento passam a ter dono `appsell_owner` (#352).

Em produção, `Configurações → Loja` respondia 403 para o próprio dono da loja.
A causa: `read_seller_settings` (0052) é SECURITY DEFINER com dono
`appsell_owner` e chama `resolve_seller_membership_role` (0025), cujo EXECUTE
foi dado só ao `appsell_runtime`. Quem executa o corpo de uma definer é o dono
dela — e `appsell_owner` não podia chamar a função da 0025.

Por que o teste local não pegou: no banco local (e no de testes) a 0025
nasceu com dono `appsell_owner`; em produção nasceu com dono = migrador,
porque o `set local role` do `env.py` não vale na hora do `create function`
(a mesma lição da 0051). A convenção do projeto é `alter function … owner to
appsell_owner` em toda função; as seis da família de recebimentos (0021–0025)
são as que ficaram fora dela:

- `record_payout_repoint_denied`, `resolve_payout_destination`,
  `resolve_seller_membership_role`, `resolve_seller_payout_account`,
  `sync_payout_account_status`, `upsert_seller_payout_account`.

Esta migration alinha as seis à convenção e reafirma o EXECUTE do runtime.
O dono passa a ser o mesmo em todo ambiente, e o teste de classe
(`test_postgres_configuracoes_da_loja`) passa a exigir `appsell_owner` em toda
função de `app_private` — é o que impede a próxima.

Downgrade: não devolve o dono ao migrador (o nome dele varia por ambiente e
a convenção é a de cima); só reafirma os grants. Subir, descer e subir de novo
é idempotente.
"""

from __future__ import annotations

from alembic import op

revision = "0053_dono_das_funcoes_payout"
down_revision = "0052_configuracoes_da_loja"
branch_labels = None
depends_on = None

_BASES_DO_READY: tuple[str, ...] = (
    "app_private.schema_ready_base_0016()",
    "app_private.schema_ready_base_0017()",
)
_READY_ATE_0052 = (
    "'0043_material_role_by_media','0044_pdf_de_trezentas_paginas','0045_envio_em_partes',"
    "'0046_aula_de_meio_giga','0047_cota_de_armazenamento','0048_nome_de_quem_entrou',"
    "'0049_caixa_de_saida_de_email','0050_recibo_do_link_de_turma',"
    "'0051_minha_conta_do_comprador','0052_configuracoes_da_loja'))"
)
_READY_ATE_0053 = (
    "'0043_material_role_by_media','0044_pdf_de_trezentas_paginas','0045_envio_em_partes',"
    "'0046_aula_de_meio_giga','0047_cota_de_armazenamento','0048_nome_de_quem_entrou',"
    "'0049_caixa_de_saida_de_email','0050_recibo_do_link_de_turma',"
    "'0051_minha_conta_do_comprador','0052_configuracoes_da_loja',"
    "'0053_dono_das_funcoes_payout'))"
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

# Assinaturas lidas do banco de produção (pg_get_function_identity_arguments).
_FUNCOES: tuple[str, ...] = (
    "app_private.record_payout_repoint_denied(uuid,text,text,uuid)",
    "app_private.resolve_payout_destination(uuid,text)",
    "app_private.resolve_seller_membership_role(uuid)",
    "app_private.resolve_seller_payout_account(uuid,text)",
    "app_private.sync_payout_account_status(text,text,boolean,boolean,boolean,text,uuid)",
    "app_private.upsert_seller_payout_account(uuid,text,text,boolean,boolean,boolean,text,uuid)",
)


def _alinhar() -> None:
    for signature in _FUNCOES:
        op.execute(f"alter function {signature} owner to appsell_owner")
        op.execute(f"revoke all on function {signature} from public")
        op.execute(f"grant execute on function {signature} to appsell_runtime")


def upgrade() -> None:
    _alinhar()
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0052)
            .replace("PARA", _READY_ATE_0053)
        )


def downgrade() -> None:
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0053)
            .replace("PARA", _READY_ATE_0052)
        )
    # O dono fica: é a convenção, e o migrador de cada ambiente tem outro nome.
    _alinhar()
