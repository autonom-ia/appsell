"""Rasteriza uma página e lê os QR dela, num processo que pode ser morto.

Existe como processo separado por causa do prazo. `zxing-cpp` é código nativo
lendo uma imagem que veio de um PDF de terceiro: chamá-lo dentro da API deixaria
o orçamento valer só até a leitura começar, e uma imagem hostil poderia segurar
o único slot do serviço sem prazo nenhum.

Aqui o pai fica com um `Popen` e um relógio. Se o tempo acaba, o grupo inteiro
morre — a rasterização e a leitura junto.

Escreve na saída um JSON com os endereços e onde eles estão, e nada mais.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .pdf_link_qr_decode import decode_page


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        return 2
    try:
        alvos = decode_page(Path(argv[1]), int(argv[2]))
    except Exception:  # noqa: BLE001 - o pai só precisa saber que não deu
        return 1
    json.dump(
        [{"url": alvo.url, "center": alvo.center, "center_x": alvo.center_x} for alvo in alvos],
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
