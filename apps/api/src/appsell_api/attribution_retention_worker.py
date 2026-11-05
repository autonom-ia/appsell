"""Retenção da atribuição de checkout (#563).

A cada hora, apaga a atribuição sem venda aprovada em 48 h e qualquer uma com
mais de 13 meses, chamando `app_private.purge_checkout_attribution` (0075) em
lotes. A trava fica no banco: com várias réplicas, só uma limpa por vez, e as
outras recebem `skipped` sem esperar.

O log leva só contagens: nada de loja, compra ou campanha.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import structlog
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

logger = structlog.get_logger(__name__)

INTERVALO_SEGUNDOS = 3600
LOTE = 5000
MAXIMO_DE_LOTES_POR_RODADA = 20


async def limpar_uma_vez(session_factory: Callable[[], Any]) -> dict[str, int | bool]:
    """Roda lotes até sobrar menos que um lote, ou até o teto por rodada."""
    total_sem_venda = 0
    total_expiradas = 0
    for _ in range(MAXIMO_DE_LOTES_POR_RODADA):
        async with session_factory() as session, session.begin():
            resultado = await session.scalar(
                text("select app_private.purge_checkout_attribution(:limite)"), {"limite": LOTE}
            )
        if not isinstance(resultado, dict) or resultado.get("skipped") is True:
            return {"skipped": True, "without_sale": total_sem_venda, "expired": total_expiradas}
        sem_venda = int(resultado.get("without_sale", 0))
        expiradas = int(resultado.get("expired", 0))
        total_sem_venda += sem_venda
        total_expiradas += expiradas
        if sem_venda < LOTE and expiradas < LOTE:
            break
    return {"skipped": False, "without_sale": total_sem_venda, "expired": total_expiradas}


async def run_attribution_retention_loop(app: FastAPI, stop: asyncio.Event) -> None:
    session_factory = getattr(app.state, "db_session_factory", None)
    if session_factory is None:
        return
    while not stop.is_set():
        try:
            resultado = await limpar_uma_vez(session_factory)
            logger.info("attribution_retention", **resultado)
        except (DBAPIError, OSError) as error:
            # Banco fora do ar: registra e tenta na próxima hora, sem derrubar
            # a task em silêncio.
            logger.warning("attribution_retention_retry", error_type=type(error).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=INTERVALO_SEGUNDOS)
        except TimeoutError:
            pass
