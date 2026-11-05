#!/usr/bin/env python3
"""A cadeia de migrations tem uma cabeça só e nenhum número repetido (#551).

Com várias trilhas em paralelo, dois PRs verdes podem criar a mesma `0066` ou
partir do mesmo pai. Cada um passa sozinho; juntos, a main fica com duas
cabeças e o `alembic upgrade head` recusa. Esta checagem roda em todo evento
do CI, inclusive na fila de merge, sobre o resultado integrado.

Lê os arquivos com `ast`, sem importar nem executar nada do PR. `revision` e
`down_revision` precisam ser literais (texto, ou tupla de textos no merge)
atribuídos uma vez no topo do módulo; qualquer outra forma falha com mensagem
clara, e o que não der para ler é falha: "não deu para checar" nunca vira
aprovado.

Modelo de ameaça: erro honesto entre PRs paralelos. Autor malicioso está fora:
a própria migration roda SQL arbitrário no upgrade, então a barreira contra
ele é a revisão, não esta leitura.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

VERSIONS = Path("apps/api/migrations/versions")
# `alembic_version.version_num` é varchar(32): uma revision maior derruba o
# `alembic upgrade` na hora de carimbar a versão.
MAX_REVISION_LENGTH = 32
NAMES = frozenset({"revision", "down_revision"})


class ChainError(RuntimeError):
    """A cadeia está quebrada ou não pôde ser lida."""


def _assigned_literals(path: Path) -> dict[str, object]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError) as erro:
        raise ChainError(f"{path.name}: não deu para ler ({erro.__class__.__name__})") from erro
    values: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        elif isinstance(node, ast.AugAssign):
            targets, value = [node.target], None
        else:
            continue
        names = {n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)} & NAMES
        if not names:
            continue
        simple = len(targets) == 1 and isinstance(targets[0], ast.Name) and value is not None
        if not simple:
            raise ChainError(f"{path.name}: {', '.join(sorted(names))} precisa ser `nome = literal`, sozinho")
        name = targets[0].id
        if name in values:
            raise ChainError(f"{path.name}: {name} atribuído mais de uma vez")
        try:
            values[name] = ast.literal_eval(value)
        except ValueError as erro:
            raise ChainError(f"{path.name}: {name} não é um valor literal") from erro
    return values


def _parents(path: Path, down: object) -> tuple[str, ...]:
    if down is None:
        return ()
    if isinstance(down, str):
        return (down,)
    if isinstance(down, (tuple, list)) and down and all(isinstance(item, str) for item in down):
        return tuple(down)
    raise ChainError(f"{path.name}: down_revision inválido")


def check_chain(versions: Path) -> str:
    """Devolve a cabeça única; levanta ChainError em qualquer outro caso."""
    if not versions.is_dir():
        raise ChainError(f"{versions}: pasta de migrations não encontrada")
    files = sorted(path for path in versions.glob("*.py") if path.name != "__init__.py")
    if not files:
        raise ChainError(f"{versions}: nenhuma migration encontrada")

    parents: dict[str, tuple[str, ...]] = {}
    by_number: dict[int, list[str]] = {}
    for path in files:
        values = _assigned_literals(path)
        revision = values.get("revision")
        if not isinstance(revision, str) or "down_revision" not in values:
            raise ChainError(f"{path.name}: revision ou down_revision ausente")
        number = path.stem.partition("_")[0]
        if not number.isdigit():
            raise ChainError(f"{path.name}: o nome não começa pelo número da migration")
        # A revision pode ser mais curta que o nome do arquivo (0028, 0030), mas
        # o número é o mesmo: é por ele que a repetição aparece.
        if revision.partition("_")[0] != number:
            raise ChainError(f"{path.name}: revision {revision!r} com outro número")
        if len(revision) > MAX_REVISION_LENGTH:
            raise ChainError(
                f"{path.name}: revision com {len(revision)} caracteres; "
                f"o alembic_version guarda {MAX_REVISION_LENGTH}"
            )
        if revision in parents:
            raise ChainError(f"revision repetida: {revision}")
        parents[revision] = _parents(path, values["down_revision"])
        # Comparado como inteiro: `5_x` e `0005_b` têm o mesmo número.
        by_number.setdefault(int(number), []).append(revision)

    repeated = {number: names for number, names in by_number.items() if len(names) > 1}
    if repeated:
        detail = "; ".join(f"{number:04d}: {', '.join(names)}" for number, names in sorted(repeated.items()))
        raise ChainError(f"número de migration repetido ({detail})")

    for revision, ups in sorted(parents.items()):
        for parent in ups:
            if parent not in parents:
                raise ChainError(f"{revision}: aponta para {parent}, que não existe")

    roots = sorted(revision for revision, ups in parents.items() if not ups)
    if len(roots) != 1:
        raise ChainError(f"a cadeia precisa de uma raiz só, achou {roots}")
    referenced = {parent for ups in parents.values() for parent in ups}
    heads = sorted(set(parents) - referenced)
    if len(heads) != 1:
        raise ChainError(f"a cadeia precisa de uma cabeça só, achou {heads}")
    # Uma raiz e uma cabeça não bastam: um ciclo (solto, pendurado sob a cabeça
    # ou um auto-laço) quebra o `alembic upgrade` com CycleDetected. Ordenação
    # topológica da raiz para a cabeça: o que sobra sem ser ordenado está num
    # ciclo ou depende de um.
    children: dict[str, list[str]] = {revision: [] for revision in parents}
    waiting = {revision: len(set(ups)) for revision, ups in parents.items()}
    for revision, ups in parents.items():
        for parent in set(ups):
            children[parent].append(revision)
    ready = [revision for revision, count in waiting.items() if count == 0]
    ordered = 0
    while ready:
        revision = ready.pop()
        ordered += 1
        for child in children[revision]:
            waiting[child] -= 1
            if waiting[child] == 0:
                ready.append(child)
    if ordered != len(parents):
        stuck = sorted(revision for revision, count in waiting.items() if count > 0)
        raise ChainError(f"ciclo na cadeia de migrations: {stuck}")
    return heads[0]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    versions = Path(args[0]) if args else VERSIONS
    try:
        head = check_chain(versions)
    except ChainError as erro:
        print(f"::error::migrations: {erro}")
        return 1
    print(f"migrations: uma cabeça só ({head}) e nenhum número repetido")
    return 0


if __name__ == "__main__":
    sys.exit(main())
