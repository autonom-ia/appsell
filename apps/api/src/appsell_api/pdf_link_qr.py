"""O destino de um botão do PDF, lido do QR code ao lado dele.

## Por que isto existe

Um PDF pode trazer o botão desenhado, o QR code impresso, e **nenhum endereço
gravado na anotação de link**. Medido no playbook do vendedor em 30/08/2026: 41
anotações `/Link`, e fora do índice todas com apenas `/BS`, `/NM`, `/Rect` e
`/Subtype`. Uma busca por qualquer endereço no arquivo inteiro devolveu zero.

Quem lê no papel escaneia o QR e chega ao vídeo. Quem lê na tela clica no botão
e não acontece nada. O aluno não tem como saber que a diferença é um campo que
o gerador do PDF esqueceu de escrever.

O endereço existe: está desenhado no QR, em pixels. Este módulo o lê de volta.

## Como

A leitura acontece num processo separado (`pdf_link_qr_worker`), que o pai pode
matar quando o prazo acaba — rasterização e decodificação juntas. `zxing-cpp` é
código nativo lendo imagem que veio de PDF de terceiro; deixá-lo no processo da
API faria o orçamento valer só até a leitura começar.

A distribuição dos QR entre os botões é **mútua**: o botão fica com o QR só
quando aquele QR também tem aquele botão como melhor opção. Ambiguidade não vira
palpite — mandar o aluno para o vídeo errado é pior do que o botão não abrir.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Final

from .pdf_link_qr_decode import QrTarget

#: Teto de páginas que aceitamos rasterizar atrás de QR.
MAX_QR_PAGES: Final = 300
#: Orçamento do documento inteiro, em segundos.
#:
#: O teto por página não bastava: um PDF com uma anotação sem destino em cada
#: página dispararia uma leitura por página, em sequência, segurando o único
#: slot do serviço por dezenas de minutos — e o cliente já teria desistido muito
#: antes. O orçamento é do documento, e fica abaixo do tempo que ele espera.
DOCUMENT_BUDGET_SECONDS: Final = 25.0
#: Distância vertical máxima, em fração da altura, entre o botão e o QR do
#: mesmo cartão.
MAX_VERTICAL_DISTANCE: Final = 0.12
#: Diferença de altura abaixo da qual dois QR contam como na mesma faixa.
#:
#: Pequeno de propósito: com folga larga, um QR mais distante na vertical
#: ganharia por estar mais perto na horizontal, e a altura deixaria de ser o que
#: decide.
VERTICAL_EPSILON: Final = 0.01
#: Folga que o segundo candidato precisa ter, na largura, para o primeiro valer.
AMBIGUITY_MARGIN: Final = 0.05


class PdfLinkQrUnavailable(RuntimeError):
    """A leitura não pôde ser feita agora."""


class QrBudget:
    """O que resta do orçamento do documento."""

    def __init__(self, seconds: float = DOCUMENT_BUDGET_SECONDS) -> None:
        self._deadline = time.monotonic() + seconds

    def remaining(self) -> float:
        return self._deadline - time.monotonic()


def read_qr_targets(
    source: Path, page_numbers: list[int], budget: QrBudget | None = None
) -> dict[int, list[QrTarget]]:
    """Os QR de todas as páginas pedidas, numa leitura só.

    Um processo por página gastava o tempo de partida do Python vezes o número
    de páginas. Medido no servidor: num PDF com vinte botões sem destino, isso
    sozinho consumia o orçamento inteiro e o documento voltava sem nenhum link
    novo — o leitor funcionava e não sobrava tempo para ele terminar.
    """
    paginas = sorted({n for n in page_numbers if 1 <= n <= MAX_QR_PAGES})
    if not paginas:
        return {}
    restante = budget.remaining() if budget is not None else DOCUMENT_BUDGET_SECONDS
    if restante <= 0:
        return {}
    try:
        processo = subprocess.Popen(  # noqa: S603 - argv only; local path, no shell
            [
                sys.executable,
                "-m",
                "appsell_api.pdf_link_qr_worker",
                str(source),
                ",".join(str(n) for n in paginas),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            # Sessão própria: no estouro morrem o filho e os netos, e não só o
            # processo que recebeu o sinal.
            start_new_session=True,
        )
    except OSError as error:
        raise PdfLinkQrUnavailable("QR reader could not start") from error
    try:
        saida, _ = processo.communicate(timeout=restante)
    except subprocess.TimeoutExpired as error:
        _kill_group(processo)
        raise PdfLinkQrUnavailable("QR reading exceeded its budget") from error
    if processo.returncode != 0 or not saida:
        return {}
    try:
        lidos = json.loads(saida)
    except json.JSONDecodeError:
        return {}
    if not isinstance(lidos, dict):
        return {}
    resultado: dict[int, list[QrTarget]] = {}
    for numero, itens in lidos.items():
        try:
            pagina = int(numero)
        except (TypeError, ValueError):
            continue
        if not isinstance(itens, list):
            continue
        resultado[pagina] = [
            QrTarget(
                url=str(item["url"]),
                center=float(item["center"]),
                center_x=float(item["center_x"]),
            )
            for item in itens
            if isinstance(item, dict) and {"url", "center", "center_x"} <= set(item)
        ]
    return resultado


def _kill_group(processo: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(os.getpgid(processo.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        processo.kill()
    try:
        processo.wait(timeout=5)
    except subprocess.TimeoutExpired:
        return


def assign_targets(
    alvos: list[QrTarget], retangulos: list[tuple[float, float]]
) -> list[str | None]:
    """Distribui os QR entre os botões da página, um para cada.

    O casamento é **mútuo**: o botão fica com o QR só quando aquele QR também
    tem aquele botão como melhor opção. Uma distribuição gulosa, na ordem em que
    as anotações aparecem no PDF, daria o QR ao primeiro botão que passasse —
    mesmo havendo outro mais alinhado logo depois.

    A altura decide, porque o cartão ocupa uma faixa da página e o QR está na
    mesma faixa do botão. A largura só desempata quando as alturas empatam, que
    é o caso de layout em colunas: somar os dois eixos não funciona, porque a
    distância horizontal entre botão e QR é a mesma para todos os candidatos e
    abafaria a diferença vertical que de fato distingue os cartões.
    """
    resultado: list[str | None] = [None] * len(retangulos)
    for indice, (centro_y, centro_x) in enumerate(retangulos):
        escolhido = _pick(alvos, centro_y, centro_x)
        if escolhido is None:
            continue
        if _pick_rectangle(retangulos, escolhido) == indice:
            resultado[indice] = escolhido.url
    return resultado


def _pick(alvos: list[QrTarget], centro_y: float, centro_x: float) -> QrTarget | None:
    perto = [alvo for alvo in alvos if abs(alvo.center - centro_y) <= MAX_VERTICAL_DISTANCE]
    if not perto:
        return None
    por_altura = sorted(perto, key=lambda alvo: abs(alvo.center - centro_y))
    if len(por_altura) == 1:
        return por_altura[0]
    primeiro = abs(por_altura[0].center - centro_y)
    if abs(por_altura[1].center - centro_y) - primeiro >= VERTICAL_EPSILON:
        return por_altura[0]
    empatados = [
        alvo
        for alvo in por_altura
        if abs(abs(alvo.center - centro_y) - primeiro) < VERTICAL_EPSILON
    ]
    por_lado = sorted(empatados, key=lambda alvo: abs(alvo.center_x - centro_x))
    folga = abs(por_lado[1].center_x - centro_x) - abs(por_lado[0].center_x - centro_x)
    return por_lado[0] if folga >= AMBIGUITY_MARGIN else None


def _pick_rectangle(retangulos: list[tuple[float, float]], alvo: QrTarget) -> int | None:
    """Qual botão este QR escolheria, pelo mesmo critério."""
    candidatos = [
        (indice, centro_y, centro_x)
        for indice, (centro_y, centro_x) in enumerate(retangulos)
        if abs(alvo.center - centro_y) <= MAX_VERTICAL_DISTANCE
    ]
    if not candidatos:
        return None
    por_altura = sorted(candidatos, key=lambda item: abs(alvo.center - item[1]))
    if len(por_altura) == 1:
        return por_altura[0][0]
    primeiro = abs(alvo.center - por_altura[0][1])
    if abs(alvo.center - por_altura[1][1]) - primeiro >= VERTICAL_EPSILON:
        return por_altura[0][0]
    empatados = [
        item for item in por_altura if abs(abs(alvo.center - item[1]) - primeiro) < VERTICAL_EPSILON
    ]
    por_lado = sorted(empatados, key=lambda item: abs(alvo.center_x - item[2]))
    folga = abs(alvo.center_x - por_lado[1][2]) - abs(alvo.center_x - por_lado[0][2])
    return por_lado[0][0] if folga >= AMBIGUITY_MARGIN else None
