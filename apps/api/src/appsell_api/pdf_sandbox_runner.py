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
3. **Ninguém remove socket de ninguém.** Esta é a que fecha a porta do
   incidente. Entre olhar um arquivo e apagá-lo há sempre um instante, e nesse
   instante o dono pode ter mudado: quem sai pode apagar o socket do sucessor, e
   quem entra pode apagar o pendente de quem está no meio do próprio arranque —
   entre o `bind` e o `listen` o arquivo existe e ninguém atende ainda.

   Não havendo remoção, não há instante. Quem publica usa `rename`, que
   substitui de forma atômica e não precisa do caminho livre. O que sobra é um
   pendente esquecido por subida que falhou, alguns bytes, e um socket sem
   ninguém atrás quando o serviço cai — que recusa conexão em vez de sumir.

Nada disto dispensa derrubar o antigo antes de subir o novo, que é como o deploy
deve ser feito. Duas publicações quase simultâneas continuam tendo um perdedor:
o processo fica vivo, com o socket aberto, e sem caminho pelo qual ser
alcançado. O desenho aqui garante que ninguém fique *sem* socket; não garante
que dois processos concorrentes façam sentido juntos.
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


def main() -> None:
    if os.geteuid() != _EXPECTED_UID or os.getegid() != _EXPECTED_GID:
        raise RuntimeError("PDF sandbox must run as the dedicated 10001:10001 user")
    _assert_directory(SOCKET_PATH.parent)
    _assert_only_our_files(SOCKET_PATH.parent)
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


def _assert_only_our_files(parent: Path) -> None:
    """O diretório é do socket e dos pendentes, e de mais nada.

    Não remove nada, de propósito. Tentar decidir se um pendente ainda serve
    era uma corrida: entre o `bind` e o `listen` de quem está subindo, o arquivo
    existe e ninguém atende — quem perguntasse por conexão concluiria que está
    abandonado e apagaria o socket de um processo vivo. Era o incidente de novo,
    por outro caminho.

    Órfãos não atrapalham: quem publica usa `rename`, que não precisa do caminho
    livre. O que sobra é um punhado de bytes por subida que falhou, e o
    diretório continua recusando qualquer coisa que não seja nossa — que é a
    parte que protege de verdade.
    """
    for entrada in sorted(parent.iterdir()):
        if entrada.name == SOCKET_PATH.name:
            continue
        if not entrada.name.startswith(PENDING_PREFIX):
            raise RuntimeError("PDF sandbox socket directory contains unexpected files")


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
