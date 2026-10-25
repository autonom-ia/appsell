"""O drift de dono criado de propósito, o pré-voo que o pega e a 0065 que conserta (#509).

Em produção, `public.seller_payout_account` nasceu com o migrador como dono.
No banco de testes ela nasce certa, então o teste de classe
(`test_toda_tabela_do_esquema_tem_dono_appsell_owner`) passa e não prova nada
sobre esse caso. Aqui o drift é recriado como em produção: o dono da tabela
passa a ser o migrador, membro de `appsell_owner`.

O que este arquivo prova:

- com o drift, o `appsell_owner` (o papel das funções definer) recebe
  `permission denied` na tabela, que é o defeito de produção;
- o pré-voo reprova e aponta a tabela, e também uma função de outro dono;
- a 0065, rodada como o migrador e partindo do papel do `env.py`, devolve o
  dono ao `appsell_owner`, sem tirar o acesso do migrador nem dar acesso ao
  runtime;
- rodá-la de novo não muda nada, e o banco limpo passa no pré-voo.

O teste devolve o banco ao estado de antes, mesmo quando falha.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

import psycopg
import pytest
from sqlalchemy.engine import make_url

from appsell_api import preflight_donos

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")
MIGRATOR_URL = os.environ.get("APPSELL_MIGRATOR_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, MIGRATOR_URL)),
    reason="isolated PostgreSQL environment is required",
)

TABELA = "public.seller_payout_account"
FUNCAO_ALHEIA = "app_private.drift_proposital_509()"
VERSOES = Path(__file__).parents[1] / "migrations" / "versions"


def _migration() -> ModuleType:
    caminho = next(VERSOES.glob("*_dono_da_conta_payout.py"))
    spec = importlib_util.spec_from_file_location("appsell_dono_0065_pg", caminho)
    assert spec is not None and spec.loader is not None
    module = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migrador() -> str:
    assert MIGRATOR_URL
    nome = make_url(MIGRATOR_URL).username
    assert nome, "a URL do migrador não tem usuário"
    return nome


def _dono(connection: psycopg.Connection, tabela: str = TABELA) -> str:
    row = connection.execute(
        "select pg_get_userbyid(relowner) from pg_class where oid=%s::regclass", (tabela,)
    ).fetchone()
    assert row is not None
    return str(row[0])


def _owner_le_a_tabela(connection: psycopg.Connection) -> bool:
    """O SELECT que as funções definer fazem, como `appsell_owner`, só lendo."""
    try:
        with connection.transaction():
            connection.execute("set transaction read only")
            connection.execute("set local role appsell_owner")
            connection.execute("select count(*) from public.seller_payout_account").fetchone()
            raise psycopg.Rollback()
    except psycopg.errors.InsufficientPrivilege:
        return False
    return True


def _rodar_0065_como_o_migrador(admin: psycopg.Connection, migration: ModuleType) -> None:
    """Como o alembic roda em produção: sessão do migrador, papel do env.py."""
    original = migration.op.execute
    migration.op.execute = lambda sql: admin.execute(sql)
    try:
        with admin.transaction():
            admin.execute(f'set local session authorization "{_migrador()}"')
            admin.execute("set local role appsell_owner")
            migration.alinhar_dono()
    finally:
        migration.op.execute = original


@pytest.fixture
def admin() -> Iterator[psycopg.Connection]:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL, autocommit=True) as connection:
        assert _dono(connection) == "appsell_owner", "o banco de testes já começou fora"
        try:
            yield connection
        finally:
            connection.execute("reset session authorization")
            connection.execute(f"alter table {TABELA} owner to appsell_owner")
            connection.execute(f"drop function if exists {FUNCAO_ALHEIA}")


def test_banco_limpo_passa_no_preflight(admin: psycopg.Connection) -> None:
    assert preflight_donos.fora_da_convencao(admin) == []
    assert ADMIN_URL
    assert preflight_donos.verificar(ADMIN_URL) == 0


def test_o_drift_de_producao_e_pego_e_a_0065_conserta(admin: psycopg.Connection) -> None:
    migrador = _migrador()
    migration = _migration()

    # Antes: o estado de produção.
    admin.execute(f'alter table {TABELA} owner to "{migrador}"')
    admin.execute(f"create function {FUNCAO_ALHEIA} returns integer language sql as 'select 1'")
    admin.execute(f'alter function {FUNCAO_ALHEIA} owner to "{migrador}"')
    assert _dono(admin) == migrador
    assert _owner_le_a_tabela(admin) is False
    fora = preflight_donos.fora_da_convencao(admin)
    assert ("relação", TABELA, migrador) in fora
    assert ("função", FUNCAO_ALHEIA, migrador) in fora
    assert ADMIN_URL
    assert preflight_donos.verificar(ADMIN_URL) == 1

    # A 0065 conserta a tabela, e só ela.
    _rodar_0065_como_o_migrador(admin, migration)
    assert _dono(admin) == "appsell_owner"
    assert _owner_le_a_tabela(admin) is True
    privilegios = admin.execute(
        "select has_table_privilege(%s,%s,'SELECT'), has_table_privilege(%s,%s,'UPDATE'), "
        "has_table_privilege('appsell_runtime',%s,'SELECT'), c.relforcerowsecurity "
        "from pg_class c where c.oid=%s::regclass",
        (migrador, TABELA, migrador, TABELA, TABELA, TABELA),
    ).fetchone()
    assert privilegios == (True, True, False, True)
    assert preflight_donos.fora_da_convencao(admin) == [("função", FUNCAO_ALHEIA, migrador)]

    # Rodar de novo não muda nada.
    _rodar_0065_como_o_migrador(admin, migration)
    assert _dono(admin) == "appsell_owner"

    admin.execute(f"drop function {FUNCAO_ALHEIA}")
    assert preflight_donos.verificar(ADMIN_URL) == 0
