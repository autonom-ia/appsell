"""Teste pulado reprova a sessão quando `APPSELL_REQUIRE_POSTGRES=1` (#450).

Os testes de banco pulam sozinhos quando falta a URL do Postgres isolado. Fora
do `test-postgres-foundation.sh` isso é o esperado. Dentro dele, um pulo quer
dizer que o teste deixou de rodar onde deveria provar alguma coisa, e o
resultado verde mente. O script liga a exigência; aqui a sessão reprova e lista
o que pulou.

Vale para qualquer pulo, não só de banco: no ambiente com Postgres nenhum teste
da lista do script pula (650 passados, 0 pulados no run 39954456152).
"""

from __future__ import annotations

import os

import pytest

EXIGENCIA = "APPSELL_REQUIRE_POSTGRES"


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if os.environ.get(EXIGENCIA) != "1":
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        # Sem o relatório não há como contar os pulos; reprovar é o seguro.
        session.exitstatus = pytest.ExitCode.USAGE_ERROR
        return
    pulados = [relatorio.nodeid for relatorio in reporter.stats.get("skipped", [])]
    if not pulados:
        return
    reporter.write_line(
        f"{EXIGENCIA}=1: {len(pulados)} teste(s) pulado(s) onde deviam rodar:", red=True
    )
    for nodeid in pulados:
        reporter.write_line(f"  {nodeid}", red=True)
    session.exitstatus = pytest.ExitCode.TESTS_FAILED
