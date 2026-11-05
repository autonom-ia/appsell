"""O pré-voo da loja que cobra na própria conta conectada (#545).

A leitura da Stripe roda contra um transporte falso; a do banco é trocada por
uma conta fixa. Cada teste diz o defeito que pega.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from uuid import UUID

import httpx
import pytest

from appsell_api import preflight_conta_conectada as preflight

SELLER = UUID("54500000-0000-4000-8000-000000000001")
CONTA = "acct_preflightLojaA"
CHAVE = "sk_test_nao_real"
URL = "postgresql://quem:senha-de-teste@127.0.0.1:1/appsell"


def _stripe(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(base_url=preflight.STRIPE_API, transport=httpx.MockTransport(handler))


def _conta_stripe(charges: object, payouts: object) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == f"/v1/accounts/{CONTA}"
        assert request.headers["authorization"] == f"Bearer {CHAVE}"
        return httpx.Response(
            200, json={"id": CONTA, "charges_enabled": charges, "payouts_enabled": payouts}
        )

    return handler


def _no_sell(monkeypatch: pytest.MonkeyPatch, conta: preflight.ContaNoSell | None) -> None:
    monkeypatch.setattr(preflight, "ler_conta_no_sell", lambda _url, _seller: conta)


def _rodar(
    capsys: pytest.CaptureFixture[str], client: httpx.Client
) -> tuple[int, dict[str, object], str]:
    codigo = preflight.verificar(SELLER, URL, CHAVE, client)
    saida = capsys.readouterr()
    return codigo, json.loads(saida.out), saida.out + saida.err


def _verificada() -> preflight.ContaNoSell:
    return preflight.ContaNoSell(CONTA, preflight.Flags(True, True))


def test_passa_so_com_a_conta_verificada_nos_dois_lados(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _no_sell(monkeypatch, _verificada())
    codigo, saida, texto = _rodar(capsys, _stripe(_conta_stripe(True, True)))
    assert codigo == 0
    assert saida["status"] == "ok"
    # Pega: o id da conta ou a chave indo para o terminal e, dali, para o repo.
    assert CONTA not in texto
    assert CHAVE not in texto


@pytest.mark.parametrize(("charges", "payouts"), [(True, False), (False, True), (False, False)])
def test_recusa_conta_que_ainda_nao_repassa_ou_nao_cobra(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    charges: bool,
    payouts: bool,
) -> None:
    # Pega: "pode cobrar" tratado como "verificada" (a Qualidade pediu os dois).
    _no_sell(monkeypatch, preflight.ContaNoSell(CONTA, preflight.Flags(charges, payouts)))
    codigo, saida, _ = _rodar(capsys, _stripe(_conta_stripe(charges, payouts)))
    assert codigo == 1
    assert saida["reason"] == "provider_not_verified"


def test_recusa_quando_o_sell_diverge_da_stripe(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Pega: o Sell com flag velho (o account.updated não chegou). A trava do
    # checkout leria esse dado, então a loja não entra.
    _no_sell(monkeypatch, preflight.ContaNoSell(CONTA, preflight.Flags(True, False)))
    codigo, saida, _ = _rodar(capsys, _stripe(_conta_stripe(True, True)))
    assert codigo == 1
    assert saida["reason"] == "sell_out_of_sync"


def test_recusa_loja_sem_conta_de_recebimento_sem_chamar_a_stripe(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def nao_chama(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("a Stripe não deve ser chamada")

    _no_sell(monkeypatch, None)
    codigo, saida, _ = _rodar(capsys, _stripe(nao_chama))
    assert codigo == 1
    assert saida == {"status": "blocked", "reason": "no_payout_account"}


@pytest.mark.parametrize(
    "resposta",
    [
        httpx.Response(401, json={"error": {"message": "Invalid API Key"}}),
        httpx.Response(500, text="erro"),
        httpx.Response(200, text="não é json"),
        httpx.Response(200, json={"id": CONTA}),
        httpx.Response(200, json={"charges_enabled": "true", "payouts_enabled": True}),
    ],
)
def test_nao_deu_para_ler_a_stripe_conta_como_nao(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    resposta: httpx.Response,
) -> None:
    # Pega: erro de leitura virando "ok" ou exibindo a resposta do provedor.
    _no_sell(monkeypatch, _verificada())
    with pytest.raises(SystemExit) as saida:
        preflight.verificar(SELLER, URL, CHAVE, _stripe(lambda _request: resposta))
    assert saida.value.code == 2
    texto = capsys.readouterr()
    assert texto.out == ""
    assert texto.err.startswith("error: stripe ")
    assert CONTA not in texto.err
    assert CHAVE not in texto.err
    assert "Invalid API Key" not in texto.err


def test_stripe_fora_do_ar_conta_como_nao(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def cai(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sem rede", request=request)

    _no_sell(monkeypatch, _verificada())
    with pytest.raises(SystemExit) as saida:
        preflight.verificar(SELLER, URL, CHAVE, _stripe(cai))
    assert saida.value.code == 2
    assert capsys.readouterr().err == "error: stripe error: ConnectError\n"


def test_banco_fora_conta_como_nao_sem_a_url(capsys: pytest.CaptureFixture[str]) -> None:
    # Porta fechada: a conexão falha antes de qualquer consulta.
    with pytest.raises(SystemExit) as saida:
        preflight.verificar(
            SELLER, URL + "?connect_timeout=2", CHAVE, _stripe(_conta_stripe(True, True))
        )
    assert saida.value.code == 2
    erro = capsys.readouterr().err
    assert erro.startswith("error: database error: ")
    assert "senha-de-teste" not in erro
    assert "127.0.0.1" not in erro


def test_so_le_o_banco() -> None:
    consulta = preflight.CONTA_DA_LOJA.lower()
    for escrita in ("insert ", "update ", "delete ", "alter ", "create ", "drop "):
        assert escrita not in consulta


@pytest.mark.parametrize(
    ("argv", "ambiente"),
    [
        ([], {}),
        (["nao-e-uuid"], {}),
        ([str(SELLER)], {"APPSELL_STRIPE_SECRET_KEY": CHAVE}),
        ([str(SELLER)], {"APPSELL_PREFLIGHT_DATABASE_URL": URL}),
    ],
)
def test_erro_de_uso_sai_com_2(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], ambiente: dict[str, str]
) -> None:
    monkeypatch.delenv(preflight.VARIAVEL_BANCO, raising=False)
    monkeypatch.delenv(preflight.VARIAVEL_CHAVE, raising=False)
    for nome, valor in ambiente.items():
        monkeypatch.setenv(nome, valor)
    with pytest.raises(SystemExit) as saida:
        preflight.main(argv)
    assert saida.value.code == 2
