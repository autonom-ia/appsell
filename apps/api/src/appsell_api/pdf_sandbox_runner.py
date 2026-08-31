"""Sobe o serviço isolado escutando num socket unix.

## Por que a troca do socket é feita com tanto cuidado

Dois processos deste serviço convivem por alguns segundos em todo deploy que não
derruba o antigo antes de subir o novo, e ambos enxergam o mesmo diretório
através de um volume compartilhado.

Em 30/08/2026 isso apagou o socket em produção. O antigo encerrou depois de o
novo ter criado o seu, e a limpeza da saída removeu o arquivo sem verificar de
quem ele era. O diretório ficou vazio; a API passou a receber "arquivo não
encontrado"; a leitura de links virou indisponibilidade; e **todo PDF passou a
abrir sem botão nenhum** — sem erro na tela e sem nada nos logs.

Três decisões evitam a repetição, e nenhuma delas depende de os dois processos
combinarem alguma coisa entre si:

1. **O nome de quem está chegando é sorteado**, não derivado do pid. Os dois
   containers têm pid 1: cada um no seu namespace, ambos escrevendo no mesmo
   volume. Um nome derivado do pid seria o mesmo nome para os dois.
2. **`listen` acontece antes de publicar.** Publicar primeiro deixaria uma
   janela com caminho no lugar e ninguém escutando, que para quem conecta é uma
   recusa — não uma espera.
3. **Ninguém remove o socket publicado.** Esta é a que fecha a porta do
   incidente: entre olhar o arquivo e apagá-lo há sempre um instante, e nesse
   instante o sucessor pode ter tomado o lugar. Não havendo remoção, não há
   instante. O `rename` do próximo processo substitui o que estiver lá, de forma
   atômica, e um socket sem ninguém atrás recusa conexão em vez de sumir — o que
   é visível, ao contrário do que aconteceu.
"""

from __future__ import annotations

import os
import secrets
import socket
import stat
from pathlib import Path

import uvicorn

SOCKET_PATH = Path("/run/appsell-pdf/pdf.sock")
#: Prefixo do socket enquanto ele ainda não tomou o lugar do anterior.
PENDING_PREFIX = ".pdf.sock."

_EXPECTED_UID = 10001
_EXPECTED_GID = 10001
#: Quanto tempo esperar ao perguntar se um socket abandonado ainda atende.
_LIVENESS_TIMEOUT_SECONDS = 0.5


def main() -> None:
    if os.geteuid() != _EXPECTED_UID or os.getegid() != _EXPECTED_GID:
        raise RuntimeError("PDF sandbox must run as the dedicated 10001:10001 user")
    _assert_directory(SOCKET_PATH.parent)
    _discard_abandoned_sockets(SOCKET_PATH.parent)
    os.umask(0o007)

    # Sorteado, e não derivado do pid: os dois containers de um deploy têm pid 1,
    # cada um no seu namespace, e escrevem no mesmo volume.
    pendente = SOCKET_PATH.parent / f"{PENDING_PREFIX}{secrets.token_hex(8)}"
    server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    publicado = False
    try:
        server_socket.bind(str(pendente))
        os.chmod(pendente, 0o660)
        _assert_socket(pendente)
        # Escutar antes de publicar: entre uma coisa e outra, quem conectasse
        # encontraria o caminho no lugar e ninguém atrás dele.
        server_socket.listen(8)
        _assert_replaceable(SOCKET_PATH)
        os.rename(pendente, SOCKET_PATH)
        publicado = True
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
        if not publicado:
            # Só o que ficou pelo caminho, e só quando nunca chegou a ser
            # publicado. O socket que está no lugar nunca é removido daqui: se
            # for o do sucessor, removê-lo é o incidente de novo.
            _discard(pendente)


def _discard(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        return


def _assert_replaceable(path: Path) -> None:
    """O que está no caminho tem de ser um socket nosso, ou nada.

    `rename` substitui em silêncio o que encontrar. Se ali estiver um arquivo
    comum ou um link, alguém pôs coisa que não é nossa num diretório que devia
    ter só o socket — e o serviço para em vez de escrever por cima.
    """
    try:
        marca = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(marca.st_mode):
        raise RuntimeError("PDF sandbox socket path holds something that is not a socket")


def _discard_abandoned_sockets(parent: Path) -> None:
    """Remove sockets pendentes que já não atendem ninguém.

    Quem decide é a própria conexão, e não o nome do arquivo: um pendente de um
    processo vivo — que fez `listen` e ainda não publicou — aceita conexão e
    fica onde está. Perguntar é o único jeito honesto: o pid no nome seria o
    mesmo para os dois containers, e a data do arquivo não diz nada sobre quem
    está do outro lado.
    """
    for entrada in sorted(parent.iterdir()):
        if entrada.name == SOCKET_PATH.name:
            continue
        if not entrada.name.startswith(PENDING_PREFIX):
            raise RuntimeError("PDF sandbox socket directory contains unexpected files")
        if not _answers(entrada):
            _discard(entrada)


def _answers(path: Path) -> bool:
    """Se há alguém escutando do outro lado deste socket."""
    sonda = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sonda.settimeout(_LIVENESS_TIMEOUT_SECONDS)
    try:
        sonda.connect(str(path))
    except OSError:
        return False
    else:
        return True
    finally:
        sonda.close()


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
