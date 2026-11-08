"""`pg_temp` no fim do `search_path` das funções da 0066 (#561).

As funções do consentimento de e-mail fixaram o `search_path` sem `pg_temp`.
Sem ele na lista, o Postgres procura o schema temporário da sessão **antes**
dos outros, e uma tabela ou função temporária com o mesmo nome poderia ser
achada no lugar da verdadeira. Hoje não dá para explorar, porque todo objeto
vem com o schema escrito, mas é a regressão que o padrão do #158 proíbe
(achado da Qualidade na 0072, conferido também aqui).

Esta migration só troca o `set search_path` de cada função, com `alter
function`. Não muda corpo, dono, grant nem comportamento. O `downgrade`
devolve o valor anterior.

Revision ID: 0076_pg_temp_no_consentimento
Revises: 0068_segunda_oferta_do_produto
"""

from __future__ import annotations

from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0076_pg_temp_no_consentimento"
down_revision = "0068_segunda_oferta_do_produto"
branch_labels = None
depends_on = None

_COMPLETO_ANTES = "pg_catalog, app_private, public"
_COMPLETO_DEPOIS = "pg_catalog, app_private, public, pg_temp"
_CATALOGO_ANTES = "pg_catalog"
_CATALOGO_DEPOIS = "pg_catalog, pg_temp"

# Assinatura, valor de antes e valor de depois.
FUNCOES: tuple[tuple[str, str, str], ...] = (
    ("app_private.normalize_email(text)", _CATALOGO_ANTES, _CATALOGO_DEPOIS),
    ("app_private.email_kind_is_transactional(text)", _CATALOGO_ANTES, _CATALOGO_DEPOIS),
    ("app_private.email_sha256(text)", _COMPLETO_ANTES, _COMPLETO_DEPOIS),
    ("app_private.email_optional_block_reason(uuid,text,text)", _COMPLETO_ANTES, _COMPLETO_DEPOIS),
    (
        "app_private.record_email_consent(uuid,text,smallint,text,uuid)",
        _COMPLETO_ANTES,
        _COMPLETO_DEPOIS,
    ),
    ("app_private.revoke_email_consent(uuid,text,uuid)", _COMPLETO_ANTES, _COMPLETO_DEPOIS),
    ("app_private.unsubscribe_email(text)", _COMPLETO_ANTES, _COMPLETO_DEPOIS),
    (
        "app_private.issue_email_unsubscribe_token(uuid,text,text)",
        _COMPLETO_ANTES,
        _COMPLETO_DEPOIS,
    ),
    ("app_private.forget_email_consents(uuid)", _COMPLETO_ANTES, _COMPLETO_DEPOIS),
)


def _carregar(revisao: str) -> ModuleType:
    caminho = Path(__file__).with_name(f"{revisao}.py")
    spec = importlib_util.spec_from_file_location(f"appsell_anterior_{revisao}", caminho)
    if spec is None or spec.loader is None:  # pragma: no cover - caminho fixo no repo
        raise RuntimeError(f"a migration {revisao} nao pode ser lida")
    modulo = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


# A 0068 não expõe `_READY_ATE_0068`: a lista dela sai de `_ready_versoes()`,
# que devolve (antes, depois). O "depois" dela é o "antes" desta.
READY_ANTES: str = _carregar(down_revision)._ready_versoes()[1]
READY_DEPOIS = READY_ANTES[:-2] + f",'{revision}'))"
# Nome que as migrations seguintes leem por `getattr` (padrão da 0067 em diante).
_READY_ATE_0068 = READY_ANTES
_READY_ATE_0076 = READY_DEPOIS
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


def _reescreve(alvo: str, de: str, para: str) -> str:
    return _REESCREVE.replace("ALVO", alvo).replace("DE", de).replace("PARA", para)


def _altera(assinatura: str, valor: str) -> str:
    return f"alter function {assinatura} set search_path = {valor}"


def upgrade() -> None:
    for assinatura, _antes, depois in FUNCOES:
        op.execute(_altera(assinatura, depois))
    for alvo in _BASES_DO_READY:
        op.execute(_reescreve(alvo, READY_ANTES, READY_DEPOIS))


def downgrade() -> None:
    for alvo in _BASES_DO_READY:
        op.execute(_reescreve(alvo, READY_DEPOIS, READY_ANTES))
    for assinatura, antes, _depois in FUNCOES:
        op.execute(_altera(assinatura, antes))
