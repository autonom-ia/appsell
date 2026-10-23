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
CAMINHOS_PROIBIDOS = (
    Path("/opt/app/.sonda-468"),
    Path("/.sonda-468"),
    Path("/run/.sonda-468"),
)
ERROS_DE_ESCRITA_NEGADA = {errno.EROFS, errno.EACCES, errno.EPERM}
# Os mesmos números do compose.yaml (pids_limit, tmpfs de /tmp).
LIMITE_DE_PROCESSOS = 64
LIMITE_DO_TMP = 2147483648


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
                permitidas.append(
                    f"{caminho} (recusou com errno {erro.errno}, não com read-only ou permissão)"
                )
            continue
        permitidas.append(str(caminho))
        caminho.unlink()
    return permitidas


def capacidades(campo: str) -> int:
    status = Path("/proc/self/status")
    if not status.is_file():
        return -1
    for linha in status.read_text().splitlines():
        if linha.startswith(f"{campo}:"):
            return int(linha.split()[1], 16)
    return -1


def limite_de_processos() -> str:
    arquivo = Path("/sys/fs/cgroup/pids.max")
    return arquivo.read_text().strip() if arquivo.is_file() else "<sem pids.max>"


def tamanho_do_tmp() -> int:
    estado = os.statvfs("/tmp")
    return estado.f_blocks * estado.f_frsize


def violacoes() -> list[str]:
    encontradas = []
    interfaces = interfaces_de_rede()
    if interfaces != ["lo"]:
        encontradas.append(f"interfaces de rede além de lo: {interfaces}")
    alcancados = rede_alcancavel()
    if alcancados:
        encontradas.append(f"rede de saída alcançável: {alcancados}")
    encontradas.extend(
        f"escrita fora do /tmp não negada: {item}" for item in escritas_permitidas()
    )
    if os.getuid() == 0:
        encontradas.append("processo roda como root")
    # CapEff é zero para qualquer uid diferente de 0, com ou sem cap-drop. Quem
    # mostra o cap-drop ALL é o conjunto limite (CapBnd): sem ele, um binário
    # setuid ainda recuperaria capabilities.
    for campo in ("CapEff", "CapBnd"):
        valor = capacidades(campo)
        if valor != 0:
            encontradas.append(f"{campo} diferente de zero: {valor:#x}")
    if limite_de_processos() != str(LIMITE_DE_PROCESSOS):
        encontradas.append(
            f"pids.max {limite_de_processos()}, esperado {LIMITE_DE_PROCESSOS}"
        )
    if not 0 < tamanho_do_tmp() <= LIMITE_DO_TMP:
        encontradas.append(
            f"/tmp com {tamanho_do_tmp()} bytes, esperado até {LIMITE_DO_TMP}"
        )
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
    print(
        "pdf-sandbox isolado: só lo, sem saída, root read-only, sem root, "
        f"CapEff e CapBnd zero, pids.max {LIMITE_DE_PROCESSOS}, /tmp até {LIMITE_DO_TMP} bytes"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
