"""Pré-voo da loja que vai cobrar na própria conta conectada (#545).

Entrar em `APPSELL_FINANCIAL_CONNECT_SELLER_IDS` é editar env, então a trava
que lê o provedor fica aqui, num passo do runbook que roda **antes** de pôr a
loja na lista. Ele só lê:

* a Stripe (`GET /v1/accounts/{conta}`, com a chave da plataforma);
* a conta de recebimentos que o Sell guarda para a loja
  (`public.seller_payout_account`), numa transação READ ONLY.

A loja só passa com a conta **verificada** nos dois lados: `charges_enabled` e
`payouts_enabled` verdadeiros na Stripe e iguais aos que o Sell guarda.
Divergência quer dizer que o `account.updated` não chegou ao Sell, e a trava do
checkout estaria olhando dado velho. Qualquer falha de leitura conta como "não".

Uso (no host, com o env do serviço; nada vai por argumento além da loja):
  APPSELL_PREFLIGHT_DATABASE_URL=postgresql://... \\
  APPSELL_STRIPE_SECRET_KEY=... \\
  python -m appsell_api.preflight_conta_conectada <seller_id>

Sai com 0 e `{"status":"ok",...}` só quando a loja pode entrar na lista. Sai
com 1 e o motivo quando não pode. Sai com 2 em erro de uso ou de leitura. A
saída mostra só os flags e o motivo: nunca o id da conta, a chave ou a URL.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any, NoReturn
from uuid import UUID

import httpx
import psycopg

from appsell_api.preflight_donos import url_do_psycopg

VARIAVEL_BANCO = "APPSELL_PREFLIGHT_DATABASE_URL"
VARIAVEL_CHAVE = "APPSELL_STRIPE_SECRET_KEY"
STRIPE_API = "https://api.stripe.com"
_TEMPO_LIMITE_BANCO = "5s"
_TEMPO_LIMITE_STRIPE = 10.0

# A tabela tem FORCE RLS com política só para `appsell_owner` (0021): a leitura
# entra nesse papel dentro da transação somente leitura.
CONTA_DA_LOJA = """
select provider_account_id, charges_enabled, payouts_enabled
from public.seller_payout_account
where seller_organization_id = %(seller)s and provider = 'stripe'
"""


class LeituraFalhou(Exception):
    """Não deu para ler um dos lados. Para o pré-voo, isso é "não"."""


@dataclass(frozen=True)
class Flags:
    charges_enabled: bool
    payouts_enabled: bool

    @property
    def verificada(self) -> bool:
        return self.charges_enabled and self.payouts_enabled

    def como_dict(self) -> dict[str, bool]:
        return {"charges_enabled": self.charges_enabled, "payouts_enabled": self.payouts_enabled}


@dataclass(frozen=True)
class ContaNoSell:
    provider_account_id: str
    flags: Flags


def ler_conta_no_sell(url: str, seller: UUID) -> ContaNoSell | None:
    try:
        with psycopg.connect(url_do_psycopg(url), autocommit=True) as connection:
            with connection.transaction():
                connection.execute("set transaction read only")
                connection.execute(f"set local statement_timeout = '{_TEMPO_LIMITE_BANCO}'")
                connection.execute("set local role appsell_owner")
                linha = connection.execute(CONTA_DA_LOJA, {"seller": seller}).fetchone()
                # Sai pelo rollback: o pré-voo não deixa nada no banco.
                raise psycopg.Rollback()
    except psycopg.Error as erro:
        raise LeituraFalhou(f"database error: {type(erro).__name__}") from None
    if linha is None:
        return None
    conta, charges, payouts = linha
    return ContaNoSell(str(conta), Flags(bool(charges), bool(payouts)))


def ler_conta_na_stripe(chave: str, conta: str, client: httpx.Client | None = None) -> Flags:
    proprio = client is None
    http = client or httpx.Client(base_url=STRIPE_API, timeout=_TEMPO_LIMITE_STRIPE)
    try:
        resposta = http.get(f"/v1/accounts/{conta}", headers={"authorization": f"Bearer {chave}"})
    except httpx.HTTPError as erro:
        raise LeituraFalhou(f"stripe error: {type(erro).__name__}") from None
    finally:
        if proprio:
            http.close()
    if resposta.status_code != 200:
        raise LeituraFalhou(f"stripe status {resposta.status_code}")
    try:
        corpo: Any = resposta.json()
    except ValueError:
        raise LeituraFalhou("stripe body is not json") from None
    charges = corpo.get("charges_enabled") if isinstance(corpo, dict) else None
    payouts = corpo.get("payouts_enabled") if isinstance(corpo, dict) else None
    if not isinstance(charges, bool) or not isinstance(payouts, bool):
        raise LeituraFalhou("stripe body without account flags")
    return Flags(charges, payouts)


def decidir(sell: ContaNoSell | None, stripe: Flags | None) -> tuple[int, dict[str, Any]]:
    # Sem conta no Sell a Stripe nem é lida; stripe só é None nesse caso.
    if sell is None or stripe is None:
        return 1, {"status": "blocked", "reason": "no_payout_account"}
    resultado: dict[str, Any] = {"stripe": stripe.como_dict(), "sell": sell.flags.como_dict()}
    if not stripe.verificada:
        return 1, {"status": "blocked", "reason": "provider_not_verified", **resultado}
    if stripe != sell.flags:
        return 1, {"status": "blocked", "reason": "sell_out_of_sync", **resultado}
    return 0, {"status": "ok", **resultado}


def verificar(seller: UUID, url: str, chave: str, client: httpx.Client | None = None) -> int:
    try:
        sell = ler_conta_no_sell(url, seller)
        stripe = (
            None if sell is None else ler_conta_na_stripe(chave, sell.provider_account_id, client)
        )
    except LeituraFalhou as erro:
        _fail(str(erro))
    codigo, saida = decidir(sell, stripe)
    print(json.dumps(saida))
    return codigo


def main(argv: list[str] | None = None) -> None:
    argumentos = sys.argv[1:] if argv is None else argv
    if len(argumentos) != 1:
        _fail("usage: python -m appsell_api.preflight_conta_conectada <seller_id>")
    try:
        seller = UUID(argumentos[0])
    except ValueError:
        _fail("seller_id must be a uuid")
    url = os.environ.get(VARIAVEL_BANCO, "")
    chave = os.environ.get(VARIAVEL_CHAVE, "")
    if not url:
        _fail(f"{VARIAVEL_BANCO} is required")
    if not chave:
        _fail(f"{VARIAVEL_CHAVE} is required")
    raise SystemExit(verificar(seller, url, chave))


def _fail(message: str) -> NoReturn:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
