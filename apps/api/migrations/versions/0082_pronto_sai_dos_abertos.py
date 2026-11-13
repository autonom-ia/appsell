"""Envio que já ficou pronto deixa de contar como envio aberto.

`create_asset_upload_intent` tem três limites de envio "aberto": 100 por
vendedor, 10 por pessoa e 4 GiB somados por vendedor. Os três contam todo
`asset_upload` que ainda tenha um `quarantine_reconcile` não concluído. Esse
job só roda cerca de duas horas depois do envio. Com isso um vídeo que já
ficou pronto (`status='ready'`) continuava ocupando a vaga por duas horas: quem
sobe dez aulas em sequência era barrado no 11º envio com tudo pronto.

Um envio pronto não está mais "em voo". O que ele guarda já é limitado por
outra regra, `seller_storage_quota_bytes()`, que soma `asset_blob`. Por isso os
três limites passam a ignorar `status='ready'`. Os outros estados continuam
contando como antes: pendente, enviado e em verificação estão em voo, e
recusado, cancelado e expirado ainda deixam o objeto na quarentena até o
reconcile limpar.

A reescrita é feita sobre o corpo que está no banco (`pg_get_functiondef`).
Cada um dos três limites tem a sua âncora, e cada âncora precisa aparecer
exatamente uma vez. Se o marcador já estiver lá, a subida não faz nada: o mesmo
SQL pode ser aplicado como hotfix antes do release sem duplicar o filtro quando
a cadeia chegar aqui.

Revision ID: 0082_pronto_sai_dos_abertos
Revises: 0081_posicao_livre_ao_criar
"""

from __future__ import annotations

from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0082_pronto_sai_dos_abertos"
down_revision = "0081_posicao_livre_ao_criar"
branch_labels = None
depends_on = None

_LOCK_TIMEOUT = "set local lock_timeout = '5s'"

INTENCAO = (
    "app_private.create_asset_upload_intent(uuid,uuid,uuid,uuid,uuid,uuid,uuid,bigint,"
    "text,text,text,bigint,text,text,text,text,uuid,text,uuid)"
)

MARCADOR = "/* 0082: pronto nao conta como aberto */"

# Os três subselects são quase iguais. O que separa um do outro é a linha que
# fecha o `exists(` e compara com o limite, por isso cada âncora leva as duas
# linhas: a do `reconcile.status` (com a indentação de cada um) e a do limite.
# O marcador vai escrito por extenso (sem interpolar) para o texto ser literal.
ANCORA_VENDEDOR = "                  and reconcile.status<>'completed'\n              ))>=100 then"
FILTRO_VENDEDOR = (
    "                  and reconcile.status<>'completed'\n"
    "              ) /* 0082: pronto nao conta como aberto */\n"
    "                and upload.status<>'ready')>=100 then"
)

ANCORA_PESSOA = "                    and reconcile.status<>'completed'\n                ))>=10 then"
FILTRO_PESSOA = (
    "                    and reconcile.status<>'completed'\n"
    "                ) /* 0082: pronto nao conta como aberto */\n"
    "                  and upload.status<>'ready')>=10 then"
)

ANCORA_BYTES = (
    "                  and reconcile.status<>'completed'\n"
    "              )),0)+p_size > app_private.seller_open_upload_quota_bytes() then"
)
FILTRO_BYTES = (
    "                  and reconcile.status<>'completed'\n"
    "              ) /* 0082: pronto nao conta como aberto */\n"
    "                and upload.status<>'ready'),0)+p_size"
    " > app_private.seller_open_upload_quota_bytes() then"
)

TROCAS: tuple[tuple[str, str], ...] = (
    (ANCORA_VENDEDOR, FILTRO_VENDEDOR),
    (ANCORA_PESSOA, FILTRO_PESSOA),
    (ANCORA_BYTES, FILTRO_BYTES),
)

# Sobe: se o marcador já está, não faz nada (hotfix aplicado antes). Senão cada
# âncora precisa aparecer uma vez só, e as três trocas entram juntas.
_SOBE = """
do $migration$
declare definicao text;
begin
  definicao := pg_get_functiondef('__ALVO__'::regprocedure);
  if position($m$__MARCADOR__$m$ in definicao) > 0 then
    return;
  end if;
  if (length(definicao)-length(replace(definicao,$a1$__ANCORA1__$a1$,'')))
       / length($a1$__ANCORA1__$a1$) <> 1
     or (length(definicao)-length(replace(definicao,$a2$__ANCORA2__$a2$,'')))
       / length($a2$__ANCORA2__$a2$) <> 1
     or (length(definicao)-length(replace(definicao,$a3$__ANCORA3__$a3$,'')))
       / length($a3$__ANCORA3__$a3$) <> 1 then
    raise exception '__ALVO__ nao continha as ancoras exatamente uma vez' using errcode='55000';
  end if;
  definicao := replace(definicao,$a1$__ANCORA1__$a1$,$f1$__FILTRO1__$f1$);
  definicao := replace(definicao,$a2$__ANCORA2__$a2$,$f2$__FILTRO2__$f2$);
  definicao := replace(definicao,$a3$__ANCORA3__$a3$,$f3$__FILTRO3__$f3$);
  execute definicao;
end $migration$
"""

# Desce: tira os três filtros, se estiverem lá. Sem o marcador, não faz nada.
# Se sobrar marcador depois da troca, o corpo foi mexido por outra mão, e descer
# pela metade seria pior do que parar.
_DESCE = """
do $migration$
declare definicao text;
begin
  definicao := pg_get_functiondef('__ALVO__'::regprocedure);
  if position($m$__MARCADOR__$m$ in definicao) = 0 then
    return;
  end if;
  definicao := replace(definicao,$f1$__FILTRO1__$f1$,$a1$__ANCORA1__$a1$);
  definicao := replace(definicao,$f2$__FILTRO2__$f2$,$a2$__ANCORA2__$a2$);
  definicao := replace(definicao,$f3$__FILTRO3__$f3$,$a3$__ANCORA3__$a3$);
  if position($m$__MARCADOR__$m$ in definicao) > 0 then
    raise exception '__ALVO__ tinha o marcador fora dos tres filtros' using errcode='55000';
  end if;
  execute definicao;
end $migration$
"""


def _sql(modelo: str) -> str:
    sql = modelo.replace("__ALVO__", INTENCAO).replace("__MARCADOR__", MARCADOR)
    for numero, (ancora, filtro) in enumerate(TROCAS, start=1):
        sql = sql.replace(f"__ANCORA{numero}__", ancora).replace(f"__FILTRO{numero}__", filtro)
    return sql


SOBE = _sql(_SOBE)
DESCE = _sql(_DESCE)

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


def _carregar(revisao: str) -> ModuleType:
    caminho = Path(__file__).with_name(f"{revisao}.py")
    spec = importlib_util.spec_from_file_location(f"appsell_anterior_{revisao}_para_0082", caminho)
    if spec is None or spec.loader is None:  # pragma: no cover - caminho fixo no repo
        raise RuntimeError(f"a migration {revisao} nao pode ser lida")
    modulo = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


_ANTERIOR = _carregar(down_revision)
READY_ANTES: str = getattr(_ANTERIOR, f"_READY_ATE_{down_revision.split('_', 1)[0]}")
READY_DEPOIS = READY_ANTES[:-2] + f",'{revision}'))"
# Nome que as migrations seguintes leem por `getattr` (padrão da 0067 em diante).
_READY_ATE_0082 = READY_DEPOIS
_BASES_DO_READY: tuple[str, ...] = _ANTERIOR._BASES_DO_READY


def upgrade() -> None:
    op.execute(_LOCK_TIMEOUT)
    op.execute(SOBE)
    for alvo in _BASES_DO_READY:
        op.execute(_reescreve(alvo, READY_ANTES, READY_DEPOIS))


def downgrade() -> None:
    op.execute(_LOCK_TIMEOUT)
    for alvo in _BASES_DO_READY:
        op.execute(_reescreve(alvo, READY_DEPOIS, READY_ANTES))
    op.execute(DESCE)
