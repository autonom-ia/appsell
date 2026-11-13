"""Envio pronto deixa de contar como aberto, e cota cheia ganha código próprio.

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

A mesma função recusava a cota de armazenamento cheia (`seller ready asset byte
quota exceeded`) com o mesmo `P4290` dos envios abertos. A API não tinha como
separar os dois casos, e a tela mandaria esperar quem precisa liberar espaço.
A cota cheia passa a usar `P4231`, e `P4290` fica só para envios abertos.
`finalize_asset_verification_v2` (0017) também levanta essa recusa, mas é
chamada pelo worker e não passa pela API; ela não muda aqui.

A reescrita é feita sobre o corpo que está no banco (`pg_get_functiondef`).
Cada uma das quatro trocas (três limites e o código da cota) tem a sua âncora,
e cada âncora precisa aparecer exatamente uma vez. Se o marcador já estiver lá, a subida não faz nada: o mesmo
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

MARCADOR = "/* 0082: limites de envio */"

# Os três subselects são quase iguais. O que separa um do outro é a linha que
# fecha o `exists(` e compara com o limite, por isso cada âncora leva as duas
# linhas: a do `reconcile.status` (com a indentação de cada um) e a do limite.
# O marcador vai escrito por extenso (sem interpolar) para o texto ser literal.
ANCORA_VENDEDOR = "                  and reconcile.status<>'completed'\n              ))>=100 then"
FILTRO_VENDEDOR = (
    "                  and reconcile.status<>'completed'\n"
    "              ) /* 0082: limites de envio */\n"
    "                and upload.status<>'ready')>=100 then"
)

ANCORA_PESSOA = "                    and reconcile.status<>'completed'\n                ))>=10 then"
FILTRO_PESSOA = (
    "                    and reconcile.status<>'completed'\n"
    "                ) /* 0082: limites de envio */\n"
    "                  and upload.status<>'ready')>=10 then"
)

ANCORA_BYTES = (
    "                  and reconcile.status<>'completed'\n"
    "              )),0)+p_size > app_private.seller_open_upload_quota_bytes() then"
)
FILTRO_BYTES = (
    "                  and reconcile.status<>'completed'\n"
    "              ) /* 0082: limites de envio */\n"
    "                and upload.status<>'ready'),0)+p_size"
    " > app_private.seller_open_upload_quota_bytes() then"
)

# A cota de armazenamento cheia ganha código próprio. A mensagem separa esta
# linha das outras recusas `P4290` da função.
ANCORA_COTA = (
    "            raise exception 'seller ready asset byte quota exceeded' using errcode='P4290';"
)
CODIGO_COTA = (
    "            raise exception 'seller ready asset byte quota exceeded' using errcode='P4231';"
    " /* 0082: limites de envio */"
)

TROCAS: tuple[tuple[str, str], ...] = (
    (ANCORA_VENDEDOR, FILTRO_VENDEDOR),
    (ANCORA_PESSOA, FILTRO_PESSOA),
    (ANCORA_BYTES, FILTRO_BYTES),
    (ANCORA_COTA, CODIGO_COTA),
)

# Sobe: se o marcador já está, não faz nada (hotfix aplicado antes). Senão cada
# âncora precisa aparecer uma vez só, e as trocas entram juntas.
_SOBE = """
do $migration$
declare definicao text;
begin
  definicao := pg_get_functiondef('__ALVO__'::regprocedure);
  if position($m$__MARCADOR__$m$ in definicao) > 0 then
    return;
  end if;
  if __CONTAGENS__ then
    raise exception '__ALVO__ nao continha as ancoras exatamente uma vez' using errcode='55000';
  end if;
__TROCAS__
  execute definicao;
end $migration$
"""

# Desce: desfaz as trocas, se estiverem lá. Sem o marcador, não faz nada.
# Se sobrar marcador depois, o corpo foi mexido por outra mão, e descer pela
# metade seria pior do que parar.
_DESCE = """
do $migration$
declare definicao text;
begin
  definicao := pg_get_functiondef('__ALVO__'::regprocedure);
  if position($m$__MARCADOR__$m$ in definicao) = 0 then
    return;
  end if;
__TROCAS__
  if position($m$__MARCADOR__$m$ in definicao) > 0 then
    raise exception '__ALVO__ tinha o marcador fora das trocas' using errcode='55000';
  end if;
  execute definicao;
end $migration$
"""


def _contagem(numero: int, ancora: str) -> str:
    texto = f"$a{numero}${ancora}$a{numero}$"
    return f"(length(definicao)-length(replace(definicao,{texto},''))) / length({texto}) <> 1"


def _troca(numero: int, de: str, para: str, de_tag: str, para_tag: str) -> str:
    return (
        f"  definicao := replace(definicao,${de_tag}{numero}${de}${de_tag}{numero}$,"
        f"${para_tag}{numero}${para}${para_tag}{numero}$);"
    )


def _sql(modelo: str, *, subindo: bool) -> str:
    numeradas = list(enumerate(TROCAS, start=1))
    contagens = "\n     or ".join(_contagem(n, ancora) for n, (ancora, _) in numeradas)
    if subindo:
        trocas = [_troca(n, ancora, novo, "a", "f") for n, (ancora, novo) in numeradas]
    else:
        trocas = [_troca(n, novo, ancora, "f", "a") for n, (ancora, novo) in numeradas]
    # As âncoras entram depois dos outros marcadores do modelo, para nenhum
    # texto delas ser trocado por engano.
    return (
        modelo.replace("__ALVO__", INTENCAO)
        .replace("__MARCADOR__", MARCADOR)
        .replace("__CONTAGENS__", contagens)
        .replace("__TROCAS__", "\n".join(trocas))
    )


SOBE = _sql(_SOBE, subindo=True)
DESCE = _sql(_DESCE, subindo=False)

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
