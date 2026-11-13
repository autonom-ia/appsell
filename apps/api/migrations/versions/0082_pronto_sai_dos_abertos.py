"""Envio pronto deixa de ocupar vaga, e cota cheia ganha código próprio.

`create_asset_upload_intent` tem três limites de envio "aberto": 100 por
vendedor, 10 por pessoa e 4 GiB somados por vendedor. Os três contam todo
`asset_upload` que ainda tenha um `quarantine_reconcile` não concluído. Esse
job só roda cerca de duas horas depois do envio. Com isso um vídeo que já
ficou pronto (`status='ready'`) continuava ocupando a vaga por duas horas: quem
sobe dez aulas em sequência era barrado no 11º envio com tudo pronto.

Os dois limites de **quantidade** (100 e 10) passam a ignorar `status='ready'`:
um envio pronto não é mais um envio em andamento. O teto de **bytes** (4 GiB)
não muda: a cópia em quarentena de um envio pronto só some com certeza quando o
reconcile completa, e até lá ela ocupa disco. Os outros estados continuam
contando nos três, como antes.

A mesma função recusava a cota de armazenamento cheia (`seller ready asset byte
quota exceeded`) com o mesmo `P4290` dos envios abertos. A API não tinha como
separar os dois casos, e a tela mandaria esperar quem precisa liberar espaço.
Nesta função a cota cheia passa a usar `P4231`, e o `P4290` dela fica só para
envios abertos. `finalize_asset_verification_v2` (0017) continua levantando
`P4290` para a mesma cota cheia; ela é chamada pelo worker, não passa pela API,
e não muda aqui.

A reescrita é feita sobre o corpo que está no banco (`pg_get_functiondef`).
Cada uma das três trocas (os dois limites de quantidade e o código da cota) tem
a sua âncora, e cada âncora precisa aparecer exatamente uma vez. Se o marcador
já estiver lá, a subida não faz nada.

Hotfix antes do release: como na 0081, aplica-se **só o bloco `SOBE`**, e não a
reescrita do readiness. A reescrita do readiness não tem marcador: ela exige
a lista antiga exatamente uma vez. Aplicada no hotfix, a subida do release não a
encontraria e falharia. Quando a cadeia chegar aqui, o `SOBE` encontra o
marcador e não duplica nada, e o readiness é reescrito normalmente.

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

# Os três subselects são quase iguais (o de bytes fica como está). O que separa
# um do outro é a linha que fecha o `exists(` e compara com o limite, por isso
# cada âncora leva as duas linhas: a do `reconcile.status` (com a indentação de
# cada um) e a do limite.
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
