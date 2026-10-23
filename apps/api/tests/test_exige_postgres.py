"""Com `APPSELL_REQUIRE_POSTGRES=1`, teste pulado reprova a sessão (#450).

No ambiente com Postgres, um `skipif` que dispara é teste de banco que deixou de
rodar sem que ninguém visse. Estes testes sobem um pytest separado, com o plugin
`exige_postgres` e um teste que sempre pula, e conferem o código de saída.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

TESTE_QUE_PULA = """
import pytest

def test_passa():
    pass

@pytest.mark.skipif(True, reason="isolated PostgreSQL environment is required")
def test_de_banco():
    pass
"""


def _roda(tmp_path: Path, exige: str | None) -> subprocess.CompletedProcess[str]:
    (tmp_path / "test_amostra.py").write_text(TESTE_QUE_PULA, encoding="utf-8")
    env = dict(os.environ)
    env.pop("APPSELL_REQUIRE_POSTGRES", None)
    if exige is not None:
        env["APPSELL_REQUIRE_POSTGRES"] = exige
    env["PYTHONPATH"] = str(TESTS_DIR)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "exige_postgres",
            "-p",
            "no:cacheprovider",
            "-q",
            "test_amostra.py",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_skip_reprova_quando_postgres_e_exigido(tmp_path: Path) -> None:
    resultado = _roda(tmp_path, "1")

    assert resultado.returncode == 1, resultado.stdout
    assert "test_amostra.py::test_de_banco" in resultado.stdout
    assert "APPSELL_REQUIRE_POSTGRES=1" in resultado.stdout


def test_skip_continua_valendo_sem_a_exigencia(tmp_path: Path) -> None:
    resultado = _roda(tmp_path, None)

    assert resultado.returncode == 0, resultado.stdout
    assert "1 skipped" in resultado.stdout


def test_outro_valor_nao_liga_a_exigencia(tmp_path: Path) -> None:
    resultado = _roda(tmp_path, "true")

    assert resultado.returncode == 0, resultado.stdout
