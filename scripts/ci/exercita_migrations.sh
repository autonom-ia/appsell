#!/usr/bin/env bash
# Exercita a cadeia de migrations num banco descartável: sobe até a cabeça,
# desce uma, e sobe de novo.
#
# Antes disto, nenhuma migration deste projeto era exercitada por automação —
# `alembic` não aparecia no workflow, e as suítes test_postgres_* não estavam
# na lista que o passo completo executa. Elas chegavam a produção validadas
# apenas por leitura, e a régua do projeto sempre pediu o contrário: descer
# tem de ser exercitado em banco descartável antes de subir.
#
# O ciclo completo, e não só o `upgrade`, porque o defeito que interessa não é
# a migration que não sobe — essa falha barulhenta na hora. É a que sobe e não
# desce, descoberta no pior momento possível: durante um rollback.
#
# O esquema exige os papéis `appsell_owner` e `appsell_runtime` já existentes;
# o migrador é um papel de login que os herda. É o mesmo desenho do bootstrap
# de produção, reduzido ao necessário para migrar.
set -euo pipefail

DB="${APPSELL_MIGRATION_TEST_DB:-appsell_migration_check}"
MIGRADOR="${APPSELL_MIGRATION_TEST_ROLE:-appsell_migrator_check}"
# Localmente o socket basta; no runner o migrador precisa de senha para o TCP.
SENHA_SQL=""
if [ -n "${APPSELL_MIGRATION_ROLE_PASSWORD:-}" ]; then
  SENHA_SQL="alter role ${MIGRADOR} password '${APPSELL_MIGRATION_ROLE_PASSWORD}';"
fi

PSQL_ADMIN=(psql -v ON_ERROR_STOP=1 -q)
if [ -n "${APPSELL_MIGRATION_ADMIN_DSN:-}" ]; then
  PSQL_ADMIN=(psql -v ON_ERROR_STOP=1 -q "${APPSELL_MIGRATION_ADMIN_DSN}")
fi

echo "==> preparando banco descartável ${DB}"
"${PSQL_ADMIN[@]}" <<SQL
drop database if exists ${DB};
select 'create role appsell_owner nologin nosuperuser nocreatedb nocreaterole noinherit nobypassrls'
where not exists(select 1 from pg_roles where rolname='appsell_owner')\gexec
select 'create role appsell_runtime nologin nosuperuser nocreatedb nocreaterole noinherit nobypassrls'
where not exists(select 1 from pg_roles where rolname='appsell_runtime')\gexec
select 'create role ${MIGRADOR} login nosuperuser nocreatedb nocreaterole inherit nobypassrls'
where not exists(select 1 from pg_roles where rolname='${MIGRADOR}')\gexec
${SENHA_SQL}
grant appsell_owner to ${MIGRADOR};
create database ${DB} owner appsell_owner;
grant connect on database ${DB} to ${MIGRADOR};
SQL

export APPSELL_MIGRATOR_DATABASE_URL="${APPSELL_MIGRATION_MIGRATOR_DSN:-postgresql+psycopg://${MIGRADOR}@/${DB}?host=/tmp}"
alembic() { (cd apps/api && uv run --project . alembic "$@"); }

echo "==> subindo a cadeia inteira"
alembic upgrade head

cabeca="$(alembic current 2>/dev/null | tail -1 | awk '{print $1}')"
test -n "${cabeca}" || { echo "não consegui ler a cabeça depois do upgrade"; exit 1; }
echo "==> cabeça aplicada: ${cabeca}"

echo "==> descendo uma migration"
alembic downgrade -1

anterior="$(alembic current 2>/dev/null | tail -1 | awk '{print $1}')"
if [ "${anterior}" = "${cabeca}" ]; then
  echo "descer não moveu a cabeça: ${cabeca}"
  exit 1
fi
echo "==> desceu para: ${anterior:-base}"

echo "==> subindo de novo"
alembic upgrade head
final="$(alembic current 2>/dev/null | tail -1 | awk '{print $1}')"
if [ "${final}" != "${cabeca}" ]; then
  echo "subir de novo não voltou à mesma cabeça: ${final} != ${cabeca}"
  exit 1
fi

echo "==> a cabeça declarada no código confere com a aplicada?"
uv run --project apps/api python - "${cabeca}" <<'PY'
import pathlib
import re
import sys

aplicada = sys.argv[1]
fonte = pathlib.Path("apps/api/src/appsell_api/reset_demo_tenant.py").read_text(encoding="utf-8")
declarada = re.search(r'EXPECTED_MIGRATION = "([^"]+)"', fonte)
if not declarada:
    sys.exit("EXPECTED_MIGRATION não encontrada em reset_demo_tenant.py")
if declarada.group(1) != aplicada:
    sys.exit(
        f"a cabeça aplicada ({aplicada}) não é a declarada em reset_demo_tenant.py "
        f"({declarada.group(1)}). Uma migration nova precisa atualizar as duas."
    )
print(f"confere: {aplicada}")
PY

echo "==> limpando"
"${PSQL_ADMIN[@]}" -c "drop database if exists ${DB}"
echo "ciclo de migrations exercitado: sobe, desce, sobe."
