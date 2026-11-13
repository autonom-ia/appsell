"""Criar aula ou módulo depois de excluir um deixa de dar 409.

Excluir (arquivar) uma aula manda a linha arquivada para a posição
`max(position)+1` do módulo e renumera as ativas de 0 a n-1. Criar uma aula
usava `count(ativas)` como posição nova. Depois de uma exclusão, a segunda aula
nova caía exatamente na posição da arquivada, e o `unique (seller, module,
position)` recusava a inserção (23505, que a API devolve como 409). Toda nova
tentativa caía no mesmo lugar: o vendedor ficava sem conseguir criar aula
naquele módulo. Módulos têm o mesmo defeito, com `course_module_position_key`.

A correção fica na criação: antes de inserir, as linhas arquivadas que estão
na posição nova ou depois dela andam uma casa. As ativas continuam de 0 a n-1,
como a reordenação e a leitura esperam, e as arquivadas continuam depois de
todas as ativas. A troca é feita com a constraint adiada, como no arquivamento.
Isso também destrava os cursos que já têm o conflito gravado, sem mexer em
dado nenhum à mão.

A reescrita é feita sobre o corpo que está no banco (`pg_get_functiondef`),
com uma âncora que precisa aparecer exatamente uma vez. Se o marcador já estiver
lá, a subida não faz nada: o mesmo SQL pode ser aplicado como hotfix antes do
release sem duplicar o deslocamento quando a cadeia chegar aqui.

Revision ID: 0081_posicao_livre_ao_criar
Revises: 0080_cancelar_devolve_o_objeto
"""

from __future__ import annotations

from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0081_posicao_livre_ao_criar"
down_revision = "0080_cancelar_devolve_o_objeto"
branch_labels = None
depends_on = None

_LOCK_TIMEOUT = "set local lock_timeout = '5s'"

AULA = "app_private.create_course_lesson(uuid,uuid,uuid,uuid,uuid,text,text,bigint,uuid,text,uuid)"
MODULO = "app_private.create_course_module(uuid,uuid,uuid,uuid,text,bigint,uuid,text,uuid)"

MARCADOR = "/* 0081: posicao livre ao criar */"

ANCORA_AULA = "insert into public.lesson(seller_organization_id,id,course_id,module_id,title,body_text,position)"
# O marcador vai escrito por extenso (sem interpolar) para o texto ser literal.
DESLOCA_AULA = (
    "/* 0081: posicao livre ao criar */\n"
    "          set constraints lesson_position_key deferred;\n"
    "          update public.lesson set position=position+1,updated_at=clock_timestamp()\n"
    "            where seller_organization_id=p_seller and course_id=p_course\n"
    "              and module_id=p_module and status='archived' and position>=next_position;\n"
    "          set constraints lesson_position_key immediate;\n"
    "          "
)

ANCORA_MODULO = (
    "insert into public.course_module(seller_organization_id,id,course_id,title,position)"
)
DESLOCA_MODULO = (
    "/* 0081: posicao livre ao criar */\n"
    "          set constraints course_module_position_key deferred;\n"
    "          update public.course_module set position=position+1,updated_at=clock_timestamp()\n"
    "            where seller_organization_id=p_seller and course_id=p_course\n"
    "              and status='archived' and position>=next_position;\n"
    "          set constraints course_module_position_key immediate;\n"
    "          "
)

# Sobe: se o marcador já está, não faz nada (hotfix aplicado antes). Senão a
# âncora precisa aparecer uma vez só, e o deslocamento entra logo antes dela.
_SOBE = """
do $migration$
declare definicao text;
begin
  definicao := pg_get_functiondef('__ALVO__'::regprocedure);
  if position($m$__MARCADOR__$m$ in definicao) > 0 then
    return;
  end if;
  if (length(definicao)-length(replace(definicao,$a$__ANCORA__$a$,'')))
       / length($a$__ANCORA__$a$) <> 1 then
    raise exception '__ALVO__ nao continha a ancora exatamente uma vez' using errcode='55000';
  end if;
  execute replace(definicao,$a$__ANCORA__$a$,$d$__DESLOCA__$d$||$a$__ANCORA__$a$);
end $migration$
"""

# Desce: tira o deslocamento, se estiver lá. Sem ele, não faz nada.
_DESCE = """
do $migration$
declare definicao text;
begin
  definicao := pg_get_functiondef('__ALVO__'::regprocedure);
  if position($m$__MARCADOR__$m$ in definicao) = 0 then
    return;
  end if;
  execute replace(definicao,$d$__DESLOCA__$d$,'');
end $migration$
"""


def _sql(modelo: str, alvo: str, ancora: str, desloca: str) -> str:
    return (
        modelo.replace("__ALVO__", alvo)
        .replace("__MARCADOR__", MARCADOR)
        .replace("__ANCORA__", ancora)
        .replace("__DESLOCA__", desloca)
    )


SOBE_AULA = _sql(_SOBE, AULA, ANCORA_AULA, DESLOCA_AULA)
SOBE_MODULO = _sql(_SOBE, MODULO, ANCORA_MODULO, DESLOCA_MODULO)
DESCE_AULA = _sql(_DESCE, AULA, ANCORA_AULA, DESLOCA_AULA)
DESCE_MODULO = _sql(_DESCE, MODULO, ANCORA_MODULO, DESLOCA_MODULO)


def _carregar(revisao: str) -> ModuleType:
    caminho = Path(__file__).with_name(f"{revisao}.py")
    spec = importlib_util.spec_from_file_location(f"appsell_anterior_{revisao}_para_0081", caminho)
    if spec is None or spec.loader is None:  # pragma: no cover - caminho fixo no repo
        raise RuntimeError(f"a migration {revisao} nao pode ser lida")
    modulo = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


_ANTERIOR = _carregar(down_revision)
READY_ANTES: str = getattr(_ANTERIOR, f"_READY_ATE_{down_revision.split('_', 1)[0]}")
READY_DEPOIS = READY_ANTES[:-2] + f",'{revision}'))"
# Nome que as migrations seguintes leem por `getattr` (padrão da 0067 em diante).
_READY_ATE_0081 = READY_DEPOIS
_BASES_DO_READY: tuple[str, ...] = _ANTERIOR._BASES_DO_READY


def upgrade() -> None:
    op.execute(_LOCK_TIMEOUT)
    op.execute(SOBE_AULA)
    op.execute(SOBE_MODULO)
    for alvo in _BASES_DO_READY:
        op.execute(_ANTERIOR._reescreve(alvo, READY_ANTES, READY_DEPOIS))


def downgrade() -> None:
    op.execute(_LOCK_TIMEOUT)
    for alvo in _BASES_DO_READY:
        op.execute(_ANTERIOR._reescreve(alvo, READY_DEPOIS, READY_ANTES))
    op.execute(DESCE_MODULO)
    op.execute(DESCE_AULA)
