"""Autonom.ia Sell domain API."""

from .logging_setup import configure_logging_from_environment

__version__ = "0.1.0"

# O log e configurado no import do pacote, e nao so no `main`. Sem isto,
# qualquer entrypoint que nao passe pelo `main` — worker, script de operacao,
# teste que importa um modulo de rota sozinho — cai no renderizador padrao do
# structlog, que foi exatamente o que produziu o log inutil de producao.
#
# A leitura e direta do ambiente porque `Settings` valida campos que um script
# pode legitimamente nao ter. `main` chama `configure_logging` de novo com os
# `Settings` ja validados, que sao a fonte de verdade e tambem leem o `.env`.
configure_logging_from_environment()
