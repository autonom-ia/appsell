#!/usr/bin/env python3
"""A cadeia de migrations tem uma cabeça só e nenhum número repetido (#551).

Com várias trilhas em paralelo, dois PRs verdes podem criar a mesma `0066` ou
partir do mesmo pai. Cada um passa sozinho; juntos, a main fica com duas
cabeças e o `alembic upgrade head` recusa. Esta checagem roda em todo evento
do CI, inclusive na fila de merge, sobre o resultado integrado.

Lê os arquivos com `ast`, sem importar nem executar nada. O que não der para
ler (sintaxe, `revision` ausente ou calculada) é falha: "não deu para checar"
nunca vira aprovado.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

VERSIONS = Path("apps/api/migrations/versions")
# `alembic_version.version_num` é varchar(32): uma revision maior falha no
# upgrade, depois de a migration já ter rodado.
MAX_REVISION_LENGTH = 32


class ChainError(RuntimeError):
    """A cadeia está quebrada ou não pôde ser lida."""


def _assigned_literals(path: Path) -> dict[str, object]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError) as erro:
        raise ChainError(f"{path.name}: não deu para ler ({erro.__class__.__name__})") from erro
    values: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue
        if not isinstance(target, ast.Name) or target.id not in {"revision", "down_revision"}:
            continue
        try:
            values[target.id] = ast.literal_eval(value)
        except ValueError as erro:
            raise ChainError(f"{path.name}: {target.id} não é um valor literal") from erro
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
    by_number: dict[str, list[str]] = {}
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
        by_number.setdefault(number, []).append(revision)

    repeated = {number: names for number, names in by_number.items() if len(names) > 1}
    if repeated:
        detail = "; ".join(f"{number}: {', '.join(names)}" for number, names in sorted(repeated.items()))
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
