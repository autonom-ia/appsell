"""Qual banco o alembic migra (#524).

O alvo é sempre `APPSELL_MIGRATOR_DATABASE_URL`. O `sqlalchemy.url` do
`alembic.ini` aponta para o banco local de desenvolvimento e só vale quando
quem roda pede isso de forma explícita e o endereço é a própria máquina. Sem
uma das duas coisas, a migration para antes de conectar: um release que perca
a variável não pode rodar em outro banco e sair com `rc=0`.
"""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

MIGRATOR_URL_ENV = "APPSELL_MIGRATOR_DATABASE_URL"
LOCAL_OPT_IN_ENV = "APPSELL_MIGRATOR_USE_LOCAL_INI"
LOCAL_OPT_IN_VALUE = "1"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class MigrationTargetError(RuntimeError):
    """O alvo da migration não está definido de forma explícita e segura."""


def resolve_migration_url(environ: Mapping[str, str], ini_url: str) -> str:
    explicit = environ.get(MIGRATOR_URL_ENV, "").strip()
    if explicit:
        return explicit
    if environ.get(LOCAL_OPT_IN_ENV) != LOCAL_OPT_IN_VALUE:
        raise MigrationTargetError(
            f"{MIGRATOR_URL_ENV} is required. To migrate the local development "
            f"database from alembic.ini, set {LOCAL_OPT_IN_ENV}={LOCAL_OPT_IN_VALUE}."
        )
    if not _is_loopback(ini_url):
        # A URL não entra na mensagem: pode trazer senha.
        raise MigrationTargetError(
            f"{LOCAL_OPT_IN_ENV} only allows a local alembic.ini target "
            "(127.0.0.1, localhost or ::1 over TCP)."
        )
    return ini_url


def _is_loopback(url: str) -> bool:
    try:
        host = make_url(url).host
    except ArgumentError:
        return False
    return host in LOOPBACK_HOSTS
