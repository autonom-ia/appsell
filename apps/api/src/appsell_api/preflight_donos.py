"""Pré-voo de donos: todo objeto do esquema pertence ao `appsell_owner` (#509).

As funções SECURITY DEFINER rodam como o dono delas, o `appsell_owner`. Uma
tabela ou função de outro dono no meio do caminho vira `permission denied`. Foi
assim com a 0025 (#352) e com `seller_payout_account` (#509). Os dois casos
nasceram só em produção, onde o migrador criou o objeto sem o `set local role`
do `env.py`. O teste de classe do CI não pega esse caso, porque lá tudo nasce
certo.

Este pré-voo roda no banco de destino antes do `alembic upgrade`. Ele só lê o
catálogo, numa transação READ ONLY, e qualquer papel com login consegue rodá-lo
(o catálogo é legível por todos). Ele lista relações, funções e tipos avulsos
de `public` e `app_private` cujo dono não é `appsell_owner`. Objetos de
extensão ficam de fora, porque pertencem a quem criou a extensão.

Uso:
  APPSELL_PREFLIGHT_DATABASE_URL=postgresql://... \\
  python -m appsell_api.preflight_donos

Sai com 0 e `{"status":"ok"}` sem nada fora da convenção. Sai com 1 e a lista
quando há algo fora. Sai com 2 em erro de uso ou de banco.
"""

from __future__ import annotations

import json
import os
import sys
from typing import NoReturn

import psycopg
from sqlalchemy.engine import make_url

DONO = "appsell_owner"
VARIAVEL = "APPSELL_PREFLIGHT_DATABASE_URL"
_TEMPO_LIMITE = "5s"

# Membros de extensão (pg_depend deptype 'e') pertencem a quem criou a
# extensão: o pgcrypto de produção é do admin, e isso é o esperado.
FORA_DA_CONVENCAO = """
with esquema as (
  select oid from pg_namespace where nspname in ('public','app_private')
), de_extensao as (
  select classid, objid from pg_depend where deptype = 'e'
)
select 'relação' as tipo, n.nspname || '.' || c.relname as nome,
  pg_get_userbyid(c.relowner) as dono
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where c.relnamespace in (select oid from esquema)
  and c.relkind in ('r','p','v','m','S','f')
  and pg_get_userbyid(c.relowner) <> %(dono)s
  and not exists (select 1 from de_extensao d
    where d.classid = 'pg_class'::regclass and d.objid = c.oid)
union all
select 'função', p.oid::regprocedure::text, pg_get_userbyid(p.proowner)
from pg_proc p
where p.pronamespace in (select oid from esquema)
  and pg_get_userbyid(p.proowner) <> %(dono)s
  and not exists (select 1 from de_extensao d
    where d.classid = 'pg_proc'::regclass and d.objid = p.oid)
union all
select 'tipo', t.oid::regtype::text, pg_get_userbyid(t.typowner)
from pg_type t left join pg_class r on r.oid = t.typrelid
where t.typnamespace in (select oid from esquema)
  and t.typtype in ('c','d','e','r','m')
  and (t.typtype <> 'c' or r.relkind = 'c')
  and pg_get_userbyid(t.typowner) <> %(dono)s
  and not exists (select 1 from de_extensao d
    where d.classid = 'pg_type'::regclass and d.objid = t.oid)
order by 1, 2
"""


def fora_da_convencao(connection: psycopg.Connection) -> list[tuple[str, str, str]]:
    """Os objetos fora da convenção, como (tipo, nome, dono), em ordem."""
    linhas = connection.execute(FORA_DA_CONVENCAO, {"dono": DONO}).fetchall()
    return [(str(tipo), str(nome), str(dono)) for tipo, nome, dono in linhas]


def url_do_psycopg(url: str) -> str:
    """Aceita também a URL do SQLAlchemy (`postgresql+asyncpg://`) da api."""
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


def _ler(url: str) -> list[tuple[str, str, str]]:
    with psycopg.connect(url_do_psycopg(url), autocommit=True) as connection:
        with connection.transaction():
            connection.execute("set transaction read only")
            connection.execute(f"set local statement_timeout = '{_TEMPO_LIMITE}'")
            objetos = fora_da_convencao(connection)
            # Sai pelo rollback: o pré-voo não deixa nada no banco.
            raise psycopg.Rollback()
    return objetos


def verificar(url: str) -> int:
    # Banco fora ou senha errada não é drift: sai com 2, como erro de uso, e a
    # mensagem leva só o tipo do erro, nunca a URL nem o texto do servidor.
    try:
        objetos = _ler(url)
    except psycopg.Error as erro:
        _fail(f"database error: {type(erro).__name__}")
    if not objetos:
        print(json.dumps({"status": "ok", "dono": DONO}))
        return 0
    print(
        json.dumps(
            {
                "status": "fora_da_convencao",
                "dono": DONO,
                "objetos": [
                    {"tipo": tipo, "nome": nome, "dono": dono} for tipo, nome, dono in objetos
                ],
            },
            ensure_ascii=False,
        )
    )
    return 1


def main() -> None:
    url = os.environ.get(VARIAVEL, "")
    if not url:
        _fail(f"{VARIAVEL} is required")
    raise SystemExit(verificar(url))


def _fail(message: str) -> NoReturn:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
