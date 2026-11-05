"""Contrato com o worker de disputa do Financial (#542).

O Financial assina os eventos de disputa exatamente como o purchase.refunded,
com o id de evento próprio. O vetor abaixo é conferido byte a byte nos dois
lados (Financial: testes do worker de disputa), então nenhum repo muda o
formato sozinho.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from appsell_api.webhook_adapters import (
    WebhookRejectedError,
    parse_generic_hmac_v1,
    verify_generic_hmac_v1,
)

SECRET = bytes([7]) * 32
ENDPOINT_KEY = "endpointkeyendpointkeyendpointkey123456"
ORDER_ID = "6f0d7c1e-8a52-4a4b-9a77-1f2e3d4c5b6a"
SELLER_ID = "4b1f0f5c-1d1f-4f6a-9a3b-2c1d0e9f8a7b"
TIMESTAMP = "1792843200"
DISPUTE_EVENT_ID = f"financial:external-checkout-dispute:{ORDER_ID}:dp_1Q2w3E4r5T6y7U8i:opened"
REFUND_EVENT_ID = f"financial:external-checkout-refund:{ORDER_ID}"
SIGNATURE = "v1=e97ee6f3f7aad1e7106beab716d1974b5408ef0ec7a0362c780234e81829d129"
PROTOCOL = "external-checkout.webhook.generic-hmac.v1"
BODY = json.dumps(
    {
        "event_type": "purchase.dispute_opened",
        "seller_id": SELLER_ID,
        "external_product_id": "prod-1",
        "external_purchase_id": ORDER_ID,
        "occurred_at": "2026-10-20T10:00:00.000Z",
        "buyer_email": "buyer@example.com",
        "buyer_identity_sub": "sub-1",
    },
    separators=(",", ":"),
).encode()


def _verify(*, event_id: str = DISPUTE_EVENT_ID, body: bytes = BODY) -> str:
    return verify_generic_hmac_v1(
        secret=SECRET,
        endpoint_key=ENDPOINT_KEY,
        timestamp=TIMESTAMP,
        event_id=event_id,
        signature=SIGNATURE,
        body=body,
        now=int(TIMESTAMP),
        max_clock_skew_seconds=300,
        protocol=PROTOCOL,
    )


def test_vetor_do_financial_verifica_e_le_como_disputa_aberta() -> None:
    # Pega: o Sell recusando os bytes exatos que o worker de disputa manda.
    _verify()
    event = parse_generic_hmac_v1(BODY)
    assert event.event_type == "purchase.dispute_opened"
    assert event.external_purchase_reference == ORDER_ID
    assert event.seller_id == SELLER_ID
    assert event.buyer_email == "buyer@example.com"
    assert event.occurred_at == datetime(2026, 10, 20, 10, 0, tzinfo=UTC)


def test_o_id_do_evento_de_disputa_cabe_no_ingresso() -> None:
    # Pega: o id com o id da disputa passando do limite de 128 do ingresso.
    assert len(DISPUTE_EVENT_ID) <= 128


@pytest.mark.parametrize(
    "event_type", ["purchase.dispute_opened", "purchase.dispute_won", "purchase.dispute_lost"]
)
def test_os_tres_tipos_de_disputa_sao_aceitos(event_type: str) -> None:
    # Pega: um dos desfechos recusado como payload inválido.
    body = BODY.replace(b'"purchase.dispute_opened"', f'"{event_type}"'.encode())
    assert parse_generic_hmac_v1(body).event_type == event_type


def test_assinatura_da_disputa_nao_vale_como_estorno() -> None:
    # Pega: uma assinatura de disputa reaproveitável sob o id do estorno.
    with pytest.raises(WebhookRejectedError):
        _verify(event_id=REFUND_EVENT_ID)


def test_desfecho_trocado_no_corpo_e_recusado() -> None:
    # Pega: "aberta" virando "ganha" com a mesma assinatura.
    won = BODY.replace(b'"purchase.dispute_opened"', b'"purchase.dispute_won"')
    with pytest.raises(WebhookRejectedError):
        _verify(body=won)


def test_campo_a_mais_continua_recusado() -> None:
    # Pega: o parser afrouxado para aceitar o id da disputa no corpo.
    payload = json.loads(BODY)
    payload["dispute_id"] = "dp_1Q2w3E4r5T6y7U8i"
    with pytest.raises(WebhookRejectedError):
        parse_generic_hmac_v1(json.dumps(payload).encode())
