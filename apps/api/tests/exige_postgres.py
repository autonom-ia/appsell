"""Teste pulado reprova a sessão quando `APPSELL_REQUIRE_POSTGRES=1` (#450).

Os testes de banco pulam sozinhos quando falta a URL do Postgres isolado. Fora
do `test-postgres-foundation.sh` isso é o esperado. Dentro dele, um pulo quer
dizer que o teste deixou de rodar onde deveria provar alguma coisa, e o
resultado verde mente. O script liga a exigência; aqui a sessão reprova e lista
o que pulou.

Vale para qualquer pulo, não só de banco: no ambiente com Postgres nenhum teste
da lista do script pula (650 passados, 0 pulados no run 39954456152).

A única exceção é o marcador `so_macos`, para teste do sandbox do sistema
(`sandbox-exec`), que só existe no macOS. Fora dele o teste pula, e esse pulo
não reprova: no Linux o isolamento é o contêiner `pdf-sandbox`, provado na
#468. A exceção vem do marcador, nunca do texto do motivo, e só cobre o pulo
que este plugin fez. Outro pulo do mesmo teste continua reprovando.
"""

from __future__ import annotations

import os
import sys

import pytest

EXIGENCIA = "APPSELL_REQUIRE_POSTGRES"
MARCADOR_SO_MACOS = "so_macos"
PLATAFORMA_MACOS = "darwin"
_PULOS_DO_MARCADOR = pytest.StashKey[set[str]]()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{MARCADOR_SO_MACOS}: teste do sandbox do sistema, que só existe no macOS (#468)",
    )
    config.stash[_PULOS_DO_MARCADOR] = set()


def pytest_runtest_setup(item: pytest.Item) -> None:
    if item.get_closest_marker(MARCADOR_SO_MACOS) is None:
        return
    if sys.platform == PLATAFORMA_MACOS:
        return
    item.config.stash[_PULOS_DO_MARCADOR].add(item.nodeid)
    pytest.skip("só macOS: no Linux o isolamento é o contêiner pdf-sandbox (#468)")


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if os.environ.get(EXIGENCIA) != "1":
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        # Sem o relatório não há como contar os pulos; reprovar é o seguro.
        session.exitstatus = pytest.ExitCode.USAGE_ERROR
        return
    do_marcador = session.config.stash[_PULOS_DO_MARCADOR]
    pulados = [
        relatorio.nodeid
        for relatorio in reporter.stats.get("skipped", [])
        if relatorio.nodeid not in do_marcador
    ]
    if not pulados:
        return
    reporter.write_line(
        f"{EXIGENCIA}=1: {len(pulados)} teste(s) pulado(s) onde deviam rodar:", red=True
    )
    for nodeid in pulados:
        reporter.write_line(f"  {nodeid}", red=True)
    session.exitstatus = pytest.ExitCode.TESTS_FAILED
