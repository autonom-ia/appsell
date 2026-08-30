"""Rasteriza as páginas pedidas e lê os QR delas, num processo que pode ser morto.

Existe como processo separado por causa do prazo. `zxing-cpp` é código nativo
lendo uma imagem que veio de um PDF de terceiro: chamá-lo dentro da API deixaria
o orçamento valer só até a leitura começar, e uma imagem hostil poderia segurar
o único slot do serviço sem prazo nenhum.

Recebe **todas** as páginas de uma vez. Um processo por página gastava o tempo
de partida do Python vezes o número de páginas — num PDF com vinte botões sem
destino, isso sozinho consumia o orçamento inteiro e o documento voltava sem
nenhum link novo.

Escreve na saída um JSON com os endereços e onde eles estão, e nada mais.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .pdf_link_qr_decode import decode_page


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        return 2
    try:
        paginas = [int(numero) for numero in argv[2].split(",")]
    except ValueError:
        return 2
    saida: dict[str, list[dict[str, object]]] = {}
    for pagina in paginas:
        try:
            alvos = decode_page(Path(argv[1]), pagina)
        except Exception:  # noqa: BLE001, S112 - uma página ruim não derruba as outras
            continue
        saida[str(pagina)] = [
            {"url": alvo.url, "center": alvo.center, "center_x": alvo.center_x} for alvo in alvos
        ]
    json.dump(saida, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
