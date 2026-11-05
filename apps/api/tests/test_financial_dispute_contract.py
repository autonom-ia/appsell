"""Contrato com o worker de disputa do Financial (#542).

O Financial assina os eventos de disputa como o purchase.refunded, com o id de
evento próprio, e acrescenta dois campos só nesses eventos: `dispute_id` (o id
da disputa na Stripe) e `dispute_created_at` (quando a disputa foi aberta lá).
O vetor abaixo é conferido byte a byte nos dois lados (Financial:
`tests/external-checkout-dispute-signing.test.ts`), então nenhum repo muda o
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
DISPUTE_ID = "dp_1Q2w3E4r5T6y7U8i"
TIMESTAMP = "1792843200"
DISPUTE_EVENT_ID = f"financial:external-checkout-dispute:{ORDER_ID}:{DISPUTE_ID}:opened"
REFUND_EVENT_ID = f"financial:external-checkout-refund:{ORDER_ID}"
SIGNATURE = "v1=ceef705b1127a33675798b789ccc01aa7c40b11d2c68655e53654c0718c2f1a1"
PROTOCOL = "external-checkout.webhook.generic-hmac.v1"
BODY = (
    b'{"event_type":"purchase.dispute_opened","seller_id":"4b1f0f5c-1d1f-4f6a-9a3b-2c1d0e9f8a7b",'
    b'"external_product_id":"prod-1","external_purchase_id":"6f0d7c1e-8a52-4a4b-9a77-1f2e3d4c5b6a",'
    b'"occurred_at":"2026-10-20T10:00:00.000Z","buyer_email":"buyer@example.com",'
    b'"buyer_identity_sub":"sub-1",'
    b'"dispute_id":"dp_1Q2w3E4r5T6y7U8i","dispute_created_at":"2026-10-20T10:00:00.000Z"}'
)


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


def _with(**changes: object) -> bytes:
    payload = json.loads(BODY)
    for key, value in changes.items():
        if value is None:
            payload.pop(key)
        else:
            payload[key] = value
    return json.dumps(payload, separators=(",", ":")).encode()


def test_vetor_do_financial_verifica_e_le_como_disputa_aberta() -> None:
    # Pega: o Sell recusando os bytes exatos que o worker de disputa manda.
    _verify()
    event = parse_generic_hmac_v1(BODY)
    assert event.event_type == "purchase.dispute_opened"
    assert event.external_purchase_reference == ORDER_ID
    assert event.seller_id == SELLER_ID
    assert event.buyer_email == "buyer@example.com"
    assert event.occurred_at == datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
    assert event.dispute_id == DISPUTE_ID
    assert event.dispute_created_at == datetime(2026, 10, 20, 10, 0, tzinfo=UTC)


def test_o_id_do_evento_de_disputa_cabe_no_ingresso() -> None:
    # Pega: o id com o id da disputa passando do limite de 128 do ingresso.
    assert len(DISPUTE_EVENT_ID) <= 128


@pytest.mark.parametrize(
    "event_type", ["purchase.dispute_opened", "purchase.dispute_won", "purchase.dispute_lost"]
)
def test_os_tres_tipos_de_disputa_sao_aceitos(event_type: str) -> None:
    # Pega: um dos desfechos recusado como payload inválido.
    assert parse_generic_hmac_v1(_with(event_type=event_type)).event_type == event_type


def test_assinatura_da_disputa_nao_vale_como_estorno() -> None:
    # Pega: uma assinatura de disputa reaproveitável sob o id do estorno.
    with pytest.raises(WebhookRejectedError):
        _verify(event_id=REFUND_EVENT_ID)


def test_desfecho_trocado_no_corpo_e_recusado() -> None:
    # Pega: "aberta" virando "ganha" com a mesma assinatura.
    won = BODY.replace(b'"purchase.dispute_opened"', b'"purchase.dispute_won"')
    with pytest.raises(WebhookRejectedError):
        _verify(body=won)


def test_id_da_disputa_trocado_no_corpo_e_recusado() -> None:
    # Pega: o estado de uma disputa gravado em outra com a mesma assinatura.
    other = BODY.replace(DISPUTE_ID.encode(), b"dp_9Z8x7C6v5B4n3M2l")
    with pytest.raises(WebhookRejectedError):
        _verify(body=other)


@pytest.mark.parametrize("missing", ["dispute_id", "dispute_created_at"])
def test_disputa_sem_os_campos_da_disputa_e_recusada(missing: str) -> None:
    # Pega: disputa sem id (sem estado por disputa) ou sem data real (sem o
    # piso que ignora disputa anterior à loja ligada).
    with pytest.raises(WebhookRejectedError):
        parse_generic_hmac_v1(_with(**{missing: None}))


@pytest.mark.parametrize("event_type", ["purchase.approved", "purchase.refunded"])
def test_campos_da_disputa_fora_de_evento_de_disputa_sao_recusados(event_type: str) -> None:
    # Pega: o parser afrouxado para aceitar o id da disputa em outro evento.
    with pytest.raises(WebhookRejectedError):
        parse_generic_hmac_v1(_with(event_type=event_type))


@pytest.mark.parametrize(
    "dispute_id",
    [
        "",
        "dp_",
        "ch_1Q2w3E4r5T6y7U8i",
        "dp_1Q2w 3E4",
        "dp_1Q2w-3E4",
        "dp_ação",
        7,
        "dp_" + "a" * 253,
    ],
)
def test_id_da_disputa_fora_do_formato_da_stripe_e_recusado(dispute_id: object) -> None:
    # Pega: lixo (ou o id de uma cobrança) tomado como id de disputa.
    with pytest.raises(WebhookRejectedError):
        parse_generic_hmac_v1(_with(dispute_id=dispute_id))


@pytest.mark.parametrize(
    "created_at", ["2026-10-20T10:00:00", "2026-10-20T07:00:00-03:00", "ontem", 7]
)
def test_data_da_disputa_tem_de_ser_utc(created_at: object) -> None:
    # Pega: a data sem fuso (ou em outro fuso) mudando o piso de ativação.
    with pytest.raises(WebhookRejectedError):
        parse_generic_hmac_v1(_with(dispute_created_at=created_at))


def test_disputa_criada_depois_do_evento_e_recusada() -> None:
    # Pega: os dois campos de data trocados, que deixariam passar disputa antiga.
    with pytest.raises(WebhookRejectedError):
        parse_generic_hmac_v1(_with(dispute_created_at="2026-10-21T10:00:00.000Z"))


def test_campo_a_mais_continua_recusado() -> None:
    # Pega: o parser afrouxado para aceitar qualquer campo.
    with pytest.raises(WebhookRejectedError):
        parse_generic_hmac_v1(_with(reason="fraudulent"))
