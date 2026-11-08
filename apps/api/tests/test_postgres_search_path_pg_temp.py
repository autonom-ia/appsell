"""Toda função `security definer` do Sell termina o search_path em pg_temp (#598).

Sem `pg_temp` explícito no fim, o Postgres procura tabelas e tipos no schema
temporário da sessão **antes** dos schemas listados. Quem pode criar tabela
temporária poderia sombrear uma tabela usada por uma função que roda com os
privilégios do dono. O padrão do #158 fixa `pg_temp` no fim.

O teste lê o catálogo vivo, depois da cadeia inteira: vale para as funções de
hoje e para as que vierem.
"""

from __future__ import annotations

import os

import psycopg
import pytest

ADMIN_URL = os.getenv("APPSELL_TEST_POSTGRES_ADMIN_URL")

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="isolated PostgreSQL environment is required")

# Sem isso, um catálogo vazio passaria sem provar nada.
MINIMO_DE_FUNCOES = 100

CONSULTA = """
select n.nspname || '.' || p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')',
       (select array_agg(c) from unnest(p.proconfig) c where c like 'search_path=%%')
from pg_proc p
join pg_namespace n on n.oid = p.pronamespace
where p.prosecdef
  and n.nspname not in ('pg_catalog', 'information_schema')
  and n.nspname not like 'pg\\_%%'
order by 1
"""


def _ultimo_do_search_path(configuracao: str) -> str:
    valor = configuracao.removeprefix("search_path=")
    return valor.split(",")[-1].strip().strip('"')


def test_toda_funcao_security_definer_termina_o_search_path_em_pg_temp() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        linhas = connection.execute(CONSULTA).fetchall()

    assert len(linhas) >= MINIMO_DE_FUNCOES
    sem_search_path = [nome for nome, configuracoes in linhas if not configuracoes]
    fora_do_padrao = [
        nome
        for nome, configuracoes in linhas
        if configuracoes and _ultimo_do_search_path(configuracoes[-1]) != "pg_temp"
    ]
    assert sem_search_path == []
    assert fora_do_padrao == [], f"{len(fora_do_padrao)} funções sem pg_temp no fim"
