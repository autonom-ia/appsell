"""Sonda de isolamento que roda dentro do contêiner pdf-sandbox (#468).

No Linux o parser de PDF roda sem sandbox do sistema: quem isola é o
contêiner. Esta sonda afirma, de dentro dele, o que a spec promete. Sai com 0
só se tudo estiver negado; senão imprime cada violação e sai com 1.

Só biblioteca padrão: roda com `python -I` na própria imagem.
"""

from __future__ import annotations

import errno
import os
import socket
import sys
import tempfile
from pathlib import Path

ALVOS_DE_REDE = (("1.1.1.1", 53), ("8.8.8.8", 443))
CAMINHOS_PROIBIDOS = (Path("/opt/app/.sonda-468"), Path("/.sonda-468"), Path("/run/.sonda-468"))
ERROS_DE_ESCRITA_NEGADA = {errno.EROFS, errno.EACCES, errno.EPERM}


def interfaces_de_rede() -> list[str]:
    pasta = Path("/sys/class/net")
    if not pasta.is_dir():
        return ["<sem /sys/class/net>"]
    return sorted(item.name for item in pasta.iterdir())


def rede_alcancavel() -> list[str]:
    alcancados = []
    for host, porta in ALVOS_DE_REDE:
        try:
            with socket.create_connection((host, porta), timeout=1):
                alcancados.append(f"{host}:{porta}")
        except OSError:
            continue
    return alcancados


def escritas_permitidas() -> list[str]:
    permitidas = []
    for caminho in CAMINHOS_PROIBIDOS:
        try:
            caminho.write_bytes(b"x")
        except OSError as erro:
            if erro.errno not in ERROS_DE_ESCRITA_NEGADA:
                permitidas.append(f"{caminho} (recusou com errno {erro.errno}, não com read-only ou permissão)")
            continue
        permitidas.append(str(caminho))
        caminho.unlink()
    return permitidas


def capacidades_efetivas() -> int:
    status = Path("/proc/self/status")
    if not status.is_file():
        return -1
    for linha in status.read_text().splitlines():
        if linha.startswith("CapEff:"):
            return int(linha.split()[1], 16)
    return -1


def violacoes() -> list[str]:
    encontradas = []
    interfaces = interfaces_de_rede()
    if interfaces != ["lo"]:
        encontradas.append(f"interfaces de rede além de lo: {interfaces}")
    alcancados = rede_alcancavel()
    if alcancados:
        encontradas.append(f"rede de saída alcançável: {alcancados}")
    encontradas.extend(f"escrita fora do /tmp não negada: {item}" for item in escritas_permitidas())
    if os.getuid() == 0:
        encontradas.append("processo roda como root")
    capacidades = capacidades_efetivas()
    if capacidades != 0:
        encontradas.append(f"capabilities efetivas diferentes de zero: {capacidades:#x}")
    # Controle: o /tmp precisa aceitar escrita, senão a sonda não distingue
    # "negado pela spec" de "a sonda não consegue escrever em lugar nenhum".
    with tempfile.NamedTemporaryFile(dir="/tmp") as controle:
        controle.write(b"ok")
    return encontradas


def main() -> int:
    encontradas = violacoes()
    for violacao in encontradas:
        print(f"VIOLAÇÃO: {violacao}")
    if encontradas:
        return 1
    print("pdf-sandbox isolado: só lo, sem saída, root read-only, sem root, sem capabilities")
    return 0


if __name__ == "__main__":
    sys.exit(main())
