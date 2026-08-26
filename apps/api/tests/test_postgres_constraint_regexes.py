"""Toda regex de CHECK CONSTRAINT tem de ser executável pelo PostgreSQL.

`lesson_material_link_url_check` usava `{3,2000}`. O POSIX do PostgreSQL limita
a contagem de repetição a **255**, então a constraint nunca chegou a testar
nada: todo INSERT levantava `2201B — invalid regular expression: invalid
repetition count(s)`.

O efeito é o pior possível. A regra parece existir, passa em revisão, e falha
só no momento em que alguém tenta usar — com um erro que a API traduziu para
503 e a tela mostrou como "Não foi possível falar com o servidor". Nenhum link
externo jamais entrou nesta base.

Uma regex inválida numa constraint é sempre defeito: ou a regra não protege
nada, ou ela impede toda escrita na tabela. Este teste executa cada uma.
"""

from __future__ import annotations

import os
import re

import psycopg
import pytest

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="isolated PostgreSQL environment is required")

# Captura os literais de regex usados com ~ ou !~ na definição da constraint.
_PATTERN = re.compile(r"!?~\s*'((?:[^']|'')*)'::text")


def _constraint_patterns() -> list[tuple[str, str, str]]:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        rows = connection.execute(
            "select conrelid::regclass::text, conname, pg_get_constraintdef(oid) "
            "from pg_constraint where contype='c' order by 1, 2"
        ).fetchall()
    found: list[tuple[str, str, str]] = []
    for tabela, nome, definicao in rows:
        for match in _PATTERN.finditer(str(definicao)):
            found.append((str(tabela), str(nome), match.group(1).replace("''", "'")))
    return found


def test_encontrou_regexes_para_conferir() -> None:
    # Sem isto, um regex de captura errado faria o teste passar sobre lista vazia.
    assert len(_constraint_patterns()) >= 10


def test_toda_regex_de_constraint_e_executavel() -> None:
    assert ADMIN_URL
    quebradas: list[str] = []
    with psycopg.connect(ADMIN_URL) as connection:
        for tabela, nome, padrao in _constraint_patterns():
            try:
                with connection.transaction():
                    connection.execute("select %s ~ %s", ("teste", padrao))
            except psycopg.errors.InvalidRegularExpression as error:
                quebradas.append(f"{tabela}.{nome}: {padrao!r} — {error}")
    assert not quebradas, "constraints com regex que o PostgreSQL não executa: " + "; ".join(
        quebradas
    )


def test_nenhuma_repeticao_passa_do_teto_do_posix() -> None:
    """O motivo exato de `{3,2000}` explodir, dito por extenso.

    Conferir a execução já cobre o caso; olhar a contagem nomeia o limite, para
    quem for escrever a próxima regex saber onde ele fica.
    """
    limite = re.compile(r"\{\s*(\d+)\s*(?:,\s*(\d+)\s*)?\}")
    excedidas: list[str] = []
    for tabela, nome, padrao in _constraint_patterns():
        for minimo, maximo in limite.findall(padrao):
            for valor in (minimo, maximo):
                if valor and int(valor) > 255:
                    excedidas.append(f"{tabela}.{nome}: repetição de {valor} (o teto é 255)")
    assert not excedidas, "; ".join(excedidas)
