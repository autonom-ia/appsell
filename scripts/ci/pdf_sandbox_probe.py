"""Sonda de isolamento que roda dentro do contêiner pdf-sandbox (#468).

No Linux o parser de PDF roda sem sandbox do sistema: quem isola é o
contêiner. Esta sonda afirma, de dentro dele, o que a spec do compose promete.

Cada checagem tem um id. A saída é uma linha `VIOLAÇÃO <id>: ...` por
violação e, no fim, `IDS: <ids>` com os ids violados. Códigos de saída:

- 0: tudo negado;
- 1: há violação (os ids dizem quais);
- 2: a sonda não conseguiu medir (erro dela, não do contêiner).

O controle negativo depende dessa separação: ele exige 1 e os ids certos. Um
"qualquer código diferente de 0" ficaria verde mesmo se a checagem de rede
quebrasse, porque outras checagens sempre disparam no controle.

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
# Os mesmos números do compose.yaml (pids_limit, tmpfs de /tmp, mem_limit).
LIMITE_DE_PROCESSOS = 64
LIMITE_DO_TMP = 2147483648
LIMITE_DE_MEMORIA = 4294967296

REDE = "rede"
ESCRITA = "escrita"
ROOT = "root"
CAPACIDADES = "capbnd"
PROCESSOS = "pids"
TMP = "tmp"
SEM_NOVOS_PRIVILEGIOS = "nonewprivs"
NOEXEC = "noexec"
MEMORIA = "memoria"


class ErroDaSonda(RuntimeError):
    """A sonda não conseguiu medir: sai com 2, não com 1."""


def campo_do_status(nome: str) -> str:
    for linha in Path("/proc/self/status").read_text().splitlines():
        if linha.startswith(f"{nome}:"):
            return linha.split()[1]
    raise ErroDaSonda(f"/proc/self/status sem {nome}")


def ler_cgroup(nome: str) -> str:
    arquivo = Path("/sys/fs/cgroup") / nome
    if not arquivo.is_file():
        raise ErroDaSonda(f"sem {arquivo} (cgroup v2 esperado)")
    return arquivo.read_text().strip()


def opcoes_do_mount(destino: str) -> list[str]:
    # mountinfo: o 5º campo é o ponto de montagem e o 6º, as opções do mount.
    for linha in Path("/proc/self/mountinfo").read_text().splitlines():
        campos = linha.split()
        if len(campos) > 5 and campos[4] == destino:
            return campos[5].split(",")
    return []


def checar_rede() -> list[str]:
    achados = []
    pasta = Path("/sys/class/net")
    if not pasta.is_dir():
        raise ErroDaSonda("sem /sys/class/net")
    interfaces = sorted(item.name for item in pasta.iterdir())
    if interfaces != ["lo"]:
        achados.append(f"interfaces além de lo: {interfaces}")
    for host, porta in ALVOS_DE_REDE:
        try:
            with socket.create_connection((host, porta), timeout=1):
                achados.append(f"saída alcançável: {host}:{porta}")
        except OSError:
            continue
    return achados


def checar_escrita() -> list[str]:
    # Só EROFS conta como negado: EACCES ou EPERM querem dizer que a permissão
    # do arquivo segurou, não o root read-only. Num contêiner gravável, o / nega
    # por permissão e esconderia a falta do read-only.
    achados = []
    for caminho in CAMINHOS_PROIBIDOS:
        try:
            caminho.write_bytes(b"x")
        except OSError as erro:
            if erro.errno != errno.EROFS:
                nome = errno.errorcode.get(erro.errno, str(erro.errno))
                achados.append(f"{caminho} recusou com {nome}, não com EROFS")
            continue
        caminho.unlink()
        achados.append(f"{caminho} gravou")
    return achados


def checar_root() -> list[str]:
    return ["processo roda como root"] if os.getuid() == 0 else []


def checar_capacidades() -> list[str]:
    # CapEff é zero para qualquer uid diferente de 0, com ou sem cap-drop. Quem
    # mostra o cap-drop ALL é o conjunto limite (CapBnd): sem ele, um binário
    # setuid ainda recuperaria capabilities.
    return [
        f"{campo} {valor}"
        for campo in ("CapEff", "CapBnd")
        if int(valor := campo_do_status(campo), 16) != 0
    ]


def checar_processos() -> list[str]:
    valor = ler_cgroup("pids.max")
    return [] if valor == str(LIMITE_DE_PROCESSOS) else [f"pids.max {valor}, esperado {LIMITE_DE_PROCESSOS}"]


def checar_tmp() -> list[str]:
    estado = os.statvfs("/tmp")
    tamanho = estado.f_blocks * estado.f_frsize
    if 0 < tamanho <= LIMITE_DO_TMP:
        return []
    return [f"/tmp com {tamanho} bytes, esperado até {LIMITE_DO_TMP}"]


def checar_sem_novos_privilegios() -> list[str]:
    valor = campo_do_status("NoNewPrivs")
    return [] if valor == "1" else [f"NoNewPrivs {valor}"]


def checar_noexec() -> list[str]:
    opcoes = opcoes_do_mount("/tmp")
    if not opcoes:
        return ["/tmp não é um mount próprio"]
    return [] if "noexec" in opcoes else [f"/tmp sem noexec: {','.join(opcoes)}"]


def checar_memoria() -> list[str]:
    valor = ler_cgroup("memory.max")
    if valor.isdigit() and 0 < int(valor) <= LIMITE_DE_MEMORIA:
        return []
    return [f"memory.max {valor}, esperado até {LIMITE_DE_MEMORIA}"]


CHECAGENS = (
    (REDE, checar_rede),
    (ESCRITA, checar_escrita),
    (ROOT, checar_root),
    (CAPACIDADES, checar_capacidades),
    (PROCESSOS, checar_processos),
    (TMP, checar_tmp),
    (SEM_NOVOS_PRIVILEGIOS, checar_sem_novos_privilegios),
    (NOEXEC, checar_noexec),
    (MEMORIA, checar_memoria),
)


def tmp_aceita_escrita() -> None:
    # Controle: o /tmp precisa aceitar escrita, senão a sonda não distingue
    # "negado pela spec" de "a sonda não consegue escrever em lugar nenhum".
    try:
        with tempfile.NamedTemporaryFile(dir="/tmp") as controle:
            controle.write(b"ok")
    except OSError as erro:
        raise ErroDaSonda(f"/tmp não aceita escrita: {erro}") from erro


def main() -> int:
    try:
        tmp_aceita_escrita()
        violados = []
        for ident, checar in CHECAGENS:
            achados = checar()
            for achado in achados:
                print(f"VIOLAÇÃO {ident}: {achado}")
            if achados:
                violados.append(ident)
    except (ErroDaSonda, OSError, ValueError) as erro:
        print(f"ERRO DA SONDA: {erro}")
        return 2
    print(f"IDS: {','.join(violados)}")
    if violados:
        return 1
    print("pdf-sandbox isolado: " + ", ".join(ident for ident, _ in CHECAGENS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
