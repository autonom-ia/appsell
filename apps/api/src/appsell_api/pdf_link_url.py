"""O endereço que um link do PDF pode carregar, num lugar só.

Extraído de `pdf_links` para que a leitura de QR use exatamente o mesmo
critério. Duas validações independentes divergiriam, e a divergência tem um
efeito específico aqui: o manifesto é validado inteiro, então um endereço que
passa na leitura e é recusado na validação **derruba todos os links do PDF** —
não só aquele.
"""

from __future__ import annotations

from typing import Final
from urllib.parse import urlsplit

MAX_PDF_LINK_URL_CHARACTERS: Final = 2_048


def safe_plain_https_url(value: str) -> str | None:
    """O endereço, ou `None` se ele não é seguro de entregar ao aluno."""
    if not 1 <= len(value) <= MAX_PDF_LINK_URL_CHARACTERS:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        # `\` é lido como `/` pelo navegador e como caractere comum por
        # `urlsplit`: um endereço com ele significa uma coisa aqui e outra lá.
        or "\\" in value
    ):
        return None
    return value
