"""Quais lojas podem ter mais de uma oferta por produto (#553, fase 2).

A lista vem da env `APPSELL_MULTI_OFFER_SELLER_IDS` (ids separados por
vírgula), vazia por padrão: ninguém tem a segunda oferta até o Rodrigo ligar
loja por loja. A Hub2You fica fora.

A api é a única fonte. O web não lê a env: usa o `multi_offer_enabled` que vem
no detalhe do produto. As funções do banco (`create_product_offer`,
`set_default_offer`) confiam nesta checagem, então **toda rota que as chama
passa por `require_multi_offer`**; um teste varre `catalog_routes.py` e falha
se alguma não passar.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from uuid import UUID

MULTI_OFFER_SELLERS_ENV = "APPSELL_MULTI_OFFER_SELLER_IDS"


class MultiOfferConfigError(ValueError):
    """A env está malformada: a rota recusa em vez de adivinhar quem está na lista."""


class MultiOfferDisabledError(PermissionError):
    """A loja não está na lista."""


def multi_offer_sellers(environ: Mapping[str, str] | None = None) -> frozenset[UUID]:
    env = os.environ if environ is None else environ
    try:
        return frozenset(
            UUID(item.strip())
            for item in env.get(MULTI_OFFER_SELLERS_ENV, "").split(",")
            if item.strip()
        )
    except ValueError as error:
        raise MultiOfferConfigError(MULTI_OFFER_SELLERS_ENV) from error


def multi_offer_enabled(seller_id: UUID, environ: Mapping[str, str] | None = None) -> bool:
    return seller_id in multi_offer_sellers(environ)


def require_multi_offer(seller_id: UUID, environ: Mapping[str, str] | None = None) -> None:
    if not multi_offer_enabled(seller_id, environ):
        raise MultiOfferDisabledError(str(seller_id))
