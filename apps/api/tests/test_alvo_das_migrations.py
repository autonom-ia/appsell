"""O alembic nunca cai em silêncio no banco do `alembic.ini` (#524).

Antes, sem `APPSELL_MIGRATOR_DATABASE_URL`, o `env.py` usava o `sqlalchemy.url`
do `alembic.ini`, que é o banco local de desenvolvimento. Um `upgrade` de
release que perdesse a variável rodava no banco errado e saía com `rc=0`.

Agora o `alembic.ini` só vale com opt-in explícito de uso local, e só se ele
apontar para a própria máquina. Qualquer outro caso falha antes de conectar.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from appsell_api.migration_target import (
    LOCAL_OPT_IN_ENV,
    MIGRATOR_URL_ENV,
    MigrationTargetError,
    resolve_migration_url,
)

API_ROOT = Path(__file__).resolve().parents[1]
INI_LOCAL = "postgresql+psycopg://appsell_migrator@127.0.0.1:55432/appsell"
DESTINO = "postgresql+psycopg://migrador:segredo@db.exemplo.test:5432/appsell"


def test_a_variavel_do_migrador_continua_sendo_o_alvo() -> None:
    url = resolve_migration_url({MIGRATOR_URL_ENV: DESTINO}, INI_LOCAL)
    assert url == DESTINO


def test_a_variavel_vence_mesmo_com_o_opt_in_local() -> None:
    url = resolve_migration_url({MIGRATOR_URL_ENV: DESTINO, LOCAL_OPT_IN_ENV: "1"}, INI_LOCAL)
    assert url == DESTINO


@pytest.mark.parametrize("ambiente", [{}, {MIGRATOR_URL_ENV: ""}, {MIGRATOR_URL_ENV: "   "}])
def test_sem_a_variavel_e_sem_opt_in_falha_alto(ambiente: dict[str, str]) -> None:
    with pytest.raises(MigrationTargetError) as erro:
        resolve_migration_url(ambiente, INI_LOCAL)
    mensagem = str(erro.value)
    assert MIGRATOR_URL_ENV in mensagem
    assert LOCAL_OPT_IN_ENV in mensagem


@pytest.mark.parametrize("valor", ["0", "true", "yes", "local", ""])
def test_o_opt_in_so_vale_com_o_valor_exato(valor: str) -> None:
    with pytest.raises(MigrationTargetError):
        resolve_migration_url({LOCAL_OPT_IN_ENV: valor}, INI_LOCAL)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_com_opt_in_o_ini_local_vale(host: str) -> None:
    ini = f"postgresql+psycopg://appsell_migrator@[{host}]:55432/appsell"
    if ":" not in host:
        ini = f"postgresql+psycopg://appsell_migrator@{host}:55432/appsell"
    assert resolve_migration_url({LOCAL_OPT_IN_ENV: "1"}, ini) == ini


@pytest.mark.parametrize(
    "ini",
    [
        "postgresql+psycopg://appsell_migrator@db.exemplo.test:5432/appsell",
        "postgresql+psycopg://appsell_migrator@10.0.0.5:5432/appsell",
        "postgresql+psycopg://appsell_migrator@/appsell",
        "isto nao e url",
    ],
)
def test_com_opt_in_o_ini_que_nao_e_local_falha(ini: str) -> None:
    with pytest.raises(MigrationTargetError) as erro:
        resolve_migration_url({LOCAL_OPT_IN_ENV: "1"}, ini)
    assert "local" in str(erro.value)


def test_a_mensagem_de_erro_nao_carrega_a_url() -> None:
    ini = "postgresql+psycopg://appsell_migrator:senha-local@db.exemplo.test:5432/appsell"
    with pytest.raises(MigrationTargetError) as erro:
        resolve_migration_url({LOCAL_OPT_IN_ENV: "1"}, ini)
    assert "senha-local" not in str(erro.value)
    assert "db.exemplo.test" not in str(erro.value)


def _alembic_current(ambiente: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = {
        chave: valor
        for chave, valor in os.environ.items()
        if chave not in (MIGRATOR_URL_ENV, LOCAL_OPT_IN_ENV)
    }
    env.update(ambiente)
    # Argumentos fixos do repo; o ambiente é o único que varia.
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", "-c", str(API_ROOT / "alembic.ini"), "current"],
        cwd=API_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_alembic_current_sem_a_variavel_falha_antes_de_conectar() -> None:
    resultado = _alembic_current({})
    assert resultado.returncode != 0
    assert "MigrationTargetError" in resultado.stderr
    assert MIGRATOR_URL_ENV in resultado.stderr
    # Falhou na escolha do alvo, não na tentativa de conectar ao banco local.
    assert "OperationalError" not in resultado.stderr
    assert "Connection refused" not in resultado.stderr
