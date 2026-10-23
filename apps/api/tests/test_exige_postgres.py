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


# O sandbox do sistema (`sandbox-exec`) só existe no macOS. No Linux o
# isolamento é o contêiner `pdf-sandbox`, provado na #468. Um teste com
# `so_macos` pula fora do macOS sem reprovar a sessão; qualquer outro pulo
# continua reprovando. A plataforma vem de uma constante do plugin, que o
# conftest da amostra troca para simular os dois lados em qualquer máquina.

TESTE_SO_MACOS = """
import pytest

@pytest.mark.so_macos
def test_do_sandbox_do_sistema():
    pass

@pytest.mark.so_macos
@pytest.mark.skipif(True, reason="outro motivo")
def test_so_macos_que_pula_por_outro_motivo():
    pass
"""

TESTE_SO_MACOS_SIMPLES = """
import pytest

@pytest.mark.so_macos
def test_do_sandbox_do_sistema():
    pass
"""


def _roda_com_plataforma(
    tmp_path: Path, corpo: str, *, macos: bool
) -> subprocess.CompletedProcess[str]:
    alvo = "sys.platform" if macos else '"plataforma-que-nao-existe"'
    (tmp_path / "conftest.py").write_text(
        f"import sys\nimport exige_postgres\nexige_postgres.PLATAFORMA_MACOS = {alvo}\n",
        encoding="utf-8",
    )
    (tmp_path / "test_amostra.py").write_text(corpo, encoding="utf-8")
    env = dict(os.environ)
    env["APPSELL_REQUIRE_POSTGRES"] = "1"
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
            "-rs",
            "test_amostra.py",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_so_macos_pula_fora_do_macos_sem_reprovar(tmp_path: Path) -> None:
    resultado = _roda_com_plataforma(tmp_path, TESTE_SO_MACOS_SIMPLES, macos=False)

    assert resultado.returncode == 0, resultado.stdout
    assert "1 skipped" in resultado.stdout
    assert "#468" in resultado.stdout


def test_so_macos_roda_no_macos(tmp_path: Path) -> None:
    resultado = _roda_com_plataforma(tmp_path, TESTE_SO_MACOS_SIMPLES, macos=True)

    assert resultado.returncode == 0, resultado.stdout
    assert "1 passed" in resultado.stdout
    assert "skipped" not in resultado.stdout


def test_no_macos_o_marcador_nao_desculpa_outro_pulo(tmp_path: Path) -> None:
    resultado = _roda_com_plataforma(tmp_path, TESTE_SO_MACOS, macos=True)

    assert resultado.returncode == 1, resultado.stdout
    assert "test_so_macos_que_pula_por_outro_motivo" in resultado.stdout
