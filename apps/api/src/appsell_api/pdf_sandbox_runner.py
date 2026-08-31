"""Sobe o serviço isolado escutando num socket unix.

## Por que a troca do socket é feita com cuidado

Dois processos deste serviço convivem por alguns segundos em todo deploy que não
derruba o antigo antes de subir o novo, e ambos enxergam o mesmo diretório. O
desenho anterior não previa isso: quem entrava apagava o socket de quem já
estava servindo, e quem saía apagava o socket de quem tinha acabado de chegar.

O segundo caso derrubou a produção em 30/08/2026. O container antigo encerrou
depois do novo ter criado o seu socket, executou a limpeza, e o diretório ficou
vazio. A API passou a receber "arquivo não encontrado" ao falar com o serviço, a
leitura de links virou `PdfLinksUnavailable`, e **todo PDF passou a abrir sem
botão nenhum** — sem erro na tela e sem nada nos logs. `healthz` continuou
dizendo que estava tudo bem, porque ele não prova o socket.

Agora cada processo cria o seu socket com um nome próprio e o coloca no lugar
com `rename`, que é atômico: quem já estava servindo continua atendendo pelo
descritor que tem na mão, e quem chega passa a receber as conexões novas. Na
saída, o processo só remove o socket se o que está no caminho ainda for o dele.
"""

from __future__ import annotations

import os
import socket
import stat
from pathlib import Path

import uvicorn

SOCKET_PATH = Path("/run/appsell-pdf/pdf.sock")
#: Prefixo do socket antes de ele tomar o lugar do anterior.
#:
#: Começa com ponto e carrega o pid: é o que permite distinguir o socket em uso
#: de um temporário que ficou para trás, e limpar o segundo sem tocar no
#: primeiro.
PENDING_PREFIX = ".pdf.sock."

_EXPECTED_UID = 10001
_EXPECTED_GID = 10001


def main() -> None:
    if os.geteuid() != _EXPECTED_UID or os.getegid() != _EXPECTED_GID:
        raise RuntimeError("PDF sandbox must run as the dedicated 10001:10001 user")
    _assert_directory(SOCKET_PATH.parent)
    _discard_abandoned_sockets(SOCKET_PATH.parent)
    os.umask(0o007)

    pendente = SOCKET_PATH.parent / f"{PENDING_PREFIX}{os.getpid()}"
    server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    identidade: tuple[int, int] | None = None
    try:
        server_socket.bind(str(pendente))
        os.chmod(pendente, 0o660)
        _assert_socket(pendente)
        # Atômico, e é o que torna a troca segura nos dois sentidos: quem já
        # estava servindo continua atendendo pelo descritor que tem na mão, e
        # nenhum instante existe sem socket no caminho.
        os.rename(pendente, SOCKET_PATH)
        identidade = _identity(SOCKET_PATH)
        server_socket.listen(8)
        config = uvicorn.Config(
            "appsell_api.pdf_sandbox_service:app",
            workers=1,
            limit_concurrency=8,
            backlog=8,
            proxy_headers=False,
            forwarded_allow_ips="",
        )
        uvicorn.Server(config).run(sockets=[server_socket])
    finally:
        server_socket.close()
        _discard_if_mine(pendente, identidade=None)
        _discard_if_mine(SOCKET_PATH, identidade=identidade)


def _identity(path: Path) -> tuple[int, int] | None:
    """Quem é este arquivo, e não apenas onde ele está.

    O caminho é o mesmo para todos os processos; o par dispositivo e inode é de
    um arquivo só. É a diferença entre remover o próprio socket e remover o de
    quem chegou depois.
    """
    try:
        marca = os.lstat(path)
    except OSError:
        return None
    return (marca.st_dev, marca.st_ino)


def _discard_if_mine(path: Path, *, identidade: tuple[int, int] | None) -> None:
    """Remove o socket só quando ele ainda é este, e não o do sucessor."""
    atual = _identity(path)
    if atual is None:
        return
    if identidade is not None and atual != identidade:
        return
    try:
        _assert_socket(path)
        path.unlink()
    except (OSError, RuntimeError):
        return


def _discard_abandoned_sockets(parent: Path) -> None:
    """Limpa temporários de processos que morreram antes de tomar o lugar.

    Nunca toca no socket em uso: um `bind` que falhou deixa para trás um arquivo
    com o pid de quem tentou, e é só esse que sai daqui.
    """
    for entrada in parent.iterdir():
        if not entrada.name.startswith(PENDING_PREFIX):
            if entrada.name != SOCKET_PATH.name:
                raise RuntimeError("PDF sandbox socket directory contains unexpected files")
            continue
        try:
            entrada.unlink()
        except OSError:
            continue


def _assert_directory(parent: Path) -> None:
    parent_stat = os.lstat(parent)
    if (
        not stat.S_ISDIR(parent_stat.st_mode)
        or stat.S_IMODE(parent_stat.st_mode) != 0o700
        or parent_stat.st_uid != _EXPECTED_UID
        or parent_stat.st_gid != _EXPECTED_GID
    ):
        raise RuntimeError("PDF sandbox socket directory ownership or mode is invalid")


def _assert_socket(path: Path) -> None:
    socket_stat = os.lstat(path)
    if (
        not stat.S_ISSOCK(socket_stat.st_mode)
        or stat.S_IMODE(socket_stat.st_mode) != 0o660
        or socket_stat.st_uid != _EXPECTED_UID
        or socket_stat.st_gid != _EXPECTED_GID
    ):
        raise RuntimeError("PDF sandbox socket ownership, type or mode is invalid")


if __name__ == "__main__":
    main()
