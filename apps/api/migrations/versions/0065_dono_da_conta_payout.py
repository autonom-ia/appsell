"""A tabela das contas de recebimento passa a ter dono `appsell_owner` (#509).

A 0053 alinhou à convenção as seis funções de recebimento (0021–0025). A
tabela que elas leem e gravam, `public.seller_payout_account` (0021), ficou de
fora. Ela nasceu com dono = migrador em produção pelo mesmo motivo da 0025: o
`set local role` do `env.py` não valia quando ela foi criada.

O efeito é o da 0053, só que na tabela. As funções são SECURITY DEFINER com
dono `appsell_owner` e rodam como ele. A tabela tem ACL vazia, então só o
dono a acessa. Um `appsell_owner` que não é o dono recebe `permission denied`
em todo caminho de payout que a lê ou grava, inclusive na 0064. No banco local
e no de testes a tabela nasceu certa, e por isso nenhum teste pegou.

A troca não tira acesso de ninguém:

- o `appsell_owner` passa a ser o dono, e a policy da 0021 (`TO appsell_owner`,
  com FORCE RLS) continua valendo para ele;
- o migrador continua alcançando a tabela, porque é membro de `appsell_owner`;
- o runtime nunca teve privilégio direto e segue passando pelas funções.

A troca é condicional: só acontece se o dono for outro. Em banco novo esta
migration não muda dono nenhum, e rodá-la duas vezes dá no mesmo.

Como na 0053, ela sai do `set local role appsell_owner` do `env.py` durante o
`alter … owner` (só o dono atual pode transferir a posse) e volta a ele
depois.

O pré-voo `python -m appsell_api.preflight_donos` lista, só lendo, todo objeto
de `public` e `app_private` fora da convenção. É ele que pega a próxima tabela
antes da migration.

Downgrade: não devolve o dono ao migrador. O nome dele varia por ambiente, a
convenção é a de cima e a volta recriaria o defeito. O downgrade só tira a
versão do readiness.
"""

from __future__ import annotations

from alembic import op

revision = "0065_dono_da_conta_payout"
down_revision = "0064_vendas_ativadas_pela_loja"
branch_labels = None
depends_on = None

TABELA = "public.seller_payout_account"

_READY_ATE_0064 = (
    "'0057_banner_na_vitrine','0058_capa_na_biblioteca','0059_canal_financial_global',"
    "'0060_atualizacao_chega_ao_aluno','0061_o_atraso_alcanca_o_aluno',"
    "'0062_mapping_do_produto_novo','0063_conciliacao_do_acesso',"
    "'0064_vendas_ativadas_pela_loja'))"
)
_READY_ATE_0065 = (
    "'0057_banner_na_vitrine','0058_capa_na_biblioteca','0059_canal_financial_global',"
    "'0060_atualizacao_chega_ao_aluno','0061_o_atraso_alcanca_o_aluno',"
    "'0062_mapping_do_produto_novo','0063_conciliacao_do_acesso',"
    "'0064_vendas_ativadas_pela_loja','0065_dono_da_conta_payout'))"
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

# Só transfere quando o dono é outro: em banco novo nada muda.
ALINHA_DONO = """
do $migration$
begin
  if (select pg_get_userbyid(relowner) from pg_class
      where oid = 'public.seller_payout_account'::regclass) <> 'appsell_owner' then
    execute 'alter table public.seller_payout_account owner to appsell_owner';
  end if;
end $migration$
"""


def alinhar_dono() -> None:
    # `alter table … owner` só é permitido ao dono atual, o migrador em
    # produção. `reset role` devolve a sessão a ele (dono da tabela e membro de
    # appsell_owner) só pelo tempo do alter; depois o papel volta ao que o
    # env.py espera. É o mesmo passo da 0053.
    op.execute("reset role")
    op.execute(ALINHA_DONO)
    op.execute("set local role appsell_owner")


def upgrade() -> None:
    alinhar_dono()
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0064)
            .replace("PARA", _READY_ATE_0065)
        )


def downgrade() -> None:
    op.execute("set local role appsell_owner")
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0065)
            .replace("PARA", _READY_ATE_0064)
        )
    # O dono fica: é a convenção, e o migrador de cada ambiente tem outro nome.
