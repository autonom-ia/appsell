"""Todo papel que uma migration cita tem de nascer no 001_roles.sh (#492).

O ensaio de restore cria, no Postgres descartável, só os papéis que
`infra/postgres/init/001_roles.sh` cria (`pg_roles_from_source`, em
`deploy/easypanel-native/backup/lib-backup.sh`). O `pg_dump` não leva papéis.
Uma migration que passe a citar um papel novo em `to`, `from` ou `role` sem
ele entrar nesse arquivo deixaria o backup sem restore provado, e só o ensaio
no host descobriria. Este teste descobre no PR.
"""

import shutil
import subprocess
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[3]
_MIGRATIONS = _RAIZ / "apps/api/migrations/versions"
_FONTE = _RAIZ / "infra/postgres/init/001_roles.sh"
_LIB = _RAIZ / "deploy/easypanel-native/backup/lib-backup.sh"

# Pontuação que separa um nome de papel do resto do SQL e do Python à volta.
_SEPARADORES = str.maketrans({caractere: " " for caractere in ",;()'\"`\n\t"})
# Palavras do SQL depois das quais vem um papel: `owner to`, `grant ... to`,
# `create policy ... to`, `revoke ... from`, `create/alter/set role`.
_ANTES_DE_PAPEL = {"to", "from", "role"}


def _papeis_da_fonte() -> set[str]:
    """Os papéis exatamente como o restore os lê, pela mesma função do shell."""
    bash = shutil.which("bash")
    assert bash is not None, "o teste precisa de bash para ler a fonte como o restore lê"
    saida = subprocess.run(  # noqa: S603
        [bash, "-c", 'source "$1" && pg_roles_from_source "$2"', "_", str(_LIB), str(_FONTE)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return set(saida.split())


def _papeis_citados() -> dict[str, set[str]]:
    """Cada `appsell_*` que vem logo depois de `to`, `from` ou `role`, por arquivo.

    Uma lista separada por vírgula (`grant ... to appsell_a, appsell_b`) entra
    inteira: os nomes seguidos que começam com `appsell_` contam todos.
    """
    citados: dict[str, set[str]] = {}
    for arquivo in sorted(_MIGRATIONS.glob("*.py")):
        palavras = arquivo.read_text(encoding="utf-8").lower().translate(_SEPARADORES).split()
        for posicao, palavra in enumerate(palavras):
            if palavra not in _ANTES_DE_PAPEL:
                continue
            seguinte = posicao + 1
            while seguinte < len(palavras) and palavras[seguinte].startswith("appsell_"):
                citados.setdefault(palavras[seguinte], set()).add(arquivo.name)
                seguinte += 1
    return citados


def test_a_fonte_rende_os_papeis_que_o_schema_usa() -> None:
    # Sem isto, uma fonte vazia ou ilegível faria o teste abaixo passar sem
    # conferir nada.
    assert {"appsell_owner", "appsell_runtime"} <= _papeis_da_fonte()


def test_as_migrations_citam_papeis_e_o_teste_os_enxerga() -> None:
    # As políticas e grants do schema citam esses dois. Se a extração parar de
    # achá-los, ela quebrou, e o teste de cobertura ficaria vazio.
    assert {"appsell_owner", "appsell_runtime"} <= set(_papeis_citados())


def test_todo_papel_citado_nas_migrations_nasce_no_001_roles() -> None:
    fora = {
        papel: sorted(arquivos)
        for papel, arquivos in _papeis_citados().items()
        if papel not in _papeis_da_fonte()
    }
    assert fora == {}, (
        "papéis citados em migrations que o infra/postgres/init/001_roles.sh "
        f"não cria (o restore ensaiado falharia): {fora}"
    )
