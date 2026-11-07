"""A cadeia de migrations lida dos arquivos: uma fonte só para os testes.

O número não é a ordem. A reserva é por trilha, e o `down_revision` é o head
da main na hora do merge: a 0068 entrou depois da 0075. Ordenar pelo nome
chamaria de topo uma migration que não é o head, ou de anterior uma que não é.
"""

from __future__ import annotations

import ast
from pathlib import Path

VERSOES = Path(__file__).resolve().parents[1] / "migrations" / "versions"


def revisoes(caminho: Path) -> tuple[str, str | None]:
    """`revision` e `down_revision` do arquivo, lidos como literais, sem importar."""
    valores: dict[str, object] = {}
    for no in ast.parse(caminho.read_text()).body:
        if isinstance(no, ast.Assign) and len(no.targets) == 1:
            alvo, valor = no.targets[0], no.value
        elif isinstance(no, ast.AnnAssign) and no.value is not None:
            alvo, valor = no.target, no.value
        else:
            continue
        if isinstance(alvo, ast.Name) and alvo.id in {"revision", "down_revision"}:
            valores[alvo.id] = ast.literal_eval(valor)
    revisao, pai = valores.get("revision"), valores.get("down_revision")
    assert isinstance(revisao, str), f"{caminho.name}: revision não é texto literal"
    assert pai is None or isinstance(pai, str), f"{caminho.name}: down_revision não é texto"
    return revisao, pai


def versoes() -> list[Path]:
    """Os arquivos de migration na ordem da cadeia, da raiz ao topo."""
    arquivos = sorted(VERSOES.glob("0*.py"))
    por_pai: dict[str | None, tuple[str, Path]] = {}
    for arquivo in arquivos:
        revisao, origem = revisoes(arquivo)
        assert origem not in por_pai, f"duas migrations partem de {origem}"
        por_pai[origem] = (revisao, arquivo)
    cadeia: list[Path] = []
    visitadas: set[str] = set()
    pai: str | None = None
    while pai in por_pai:
        pai, caminho = por_pai[pai]
        assert pai not in visitadas, f"a cadeia volta para {pai}"
        visitadas.add(pai)
        cadeia.append(caminho)
    fora = sorted(set(arquivos) - set(cadeia))
    assert not fora, f"migrations fora da cadeia: {[c.name for c in fora]}"
    return cadeia


def anterior_na_cadeia(revisao: str) -> str:
    """A revisão que vem logo antes de `revisao` na cadeia."""
    nomes = [caminho.stem for caminho in versoes()]
    posicao = nomes.index(revisao)
    assert posicao > 0, f"{revisao} é a raiz da cadeia"
    return nomes[posicao - 1]
