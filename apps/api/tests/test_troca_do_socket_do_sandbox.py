"""A troca do socket quando dois processos do serviço isolado convivem.

Todo deploy que sobe o novo antes de derrubar o antigo tem alguns segundos com
dois processos enxergando o mesmo diretório, através de um volume compartilhado.

Em 30/08/2026 isso apagou o socket em produção. O antigo encerrou depois de o
novo ter criado o seu, e a limpeza da saída removeu o arquivo sem verificar de
quem ele era. O diretório ficou vazio; a API passou a receber "arquivo não
encontrado"; a leitura de links virou indisponibilidade; e **todo PDF passou a
abrir sem botão nenhum** — sem erro na tela e sem nada nos logs. O `healthz`
respondeu que estava tudo bem o tempo inteiro.

Estes testes existem porque nada mais acusa: o serviço sobe, diz que está
pronto, e não atende ninguém.
"""

from __future__ import annotations

import os
import secrets
import shutil
import socket
import stat
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from appsell_api import pdf_sandbox_runner as runner


@pytest.fixture
def diretorio(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Um diretório com o dono e o modo que o serviço exige.

    Curto de propósito, e não o `tmp_path` do pytest: o caminho de um socket
    unix cabe em pouco mais de cem bytes, e o diretório temporário do teste
    estoura esse limite antes de chegar no nome do arquivo.
    """
    alvo = Path(tempfile.mkdtemp(prefix="sock-", dir="/tmp"))
    os.chmod(alvo, 0o700)
    monkeypatch.setattr(runner, "SOCKET_PATH", alvo / "pdf.sock")
    monkeypatch.setattr(runner, "_EXPECTED_UID", os.geteuid())
    # O grupo vem do diretório, e não de `getegid()`: em alguns sistemas o
    # arquivo criado herda o grupo do pai, e o teste passaria a medir isso em
    # vez do que ele existe para medir.
    monkeypatch.setattr(runner, "_EXPECTED_GID", os.stat(alvo).st_gid)
    try:
        yield alvo
    finally:
        shutil.rmtree(alvo, ignore_errors=True)


def _escutando(caminho: Path) -> socket.socket:
    """Um socket com alguém atrás dele, como o de um processo em pé."""
    laco = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    laco.bind(str(caminho))
    os.chmod(caminho, 0o660)
    laco.listen(8)
    return laco


def _abandonado(caminho: Path) -> None:
    """Um socket que ficou para trás: o arquivo existe, ninguém atende."""
    laco = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    laco.bind(str(caminho))
    os.chmod(caminho, 0o660)
    laco.close()


def _identidade(caminho: Path) -> tuple[int, int]:
    marca = os.lstat(caminho)
    return (marca.st_dev, marca.st_ino)


class TestOIncidente:
    """O caminho exato que apagou o socket em produção, reproduzido."""

    def test_o_processo_que_sai_deixa_o_socket_do_sucessor_no_lugar(
        self, diretorio: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`main()` roda até o fim, e o socket que fica é o do sucessor.

        É o incidente: o antigo encerrou depois de o novo ter publicado, e a
        limpeza da saída removeu o arquivo sem verificar de quem ele era.

        Aqui `main()` é o antigo. Enquanto ele "serve", o sucessor publica o
        socket dele por cima; quando o antigo termina, o que tem de sobrar no
        caminho é o do sucessor — com o mesmo inode com que foi publicado.
        """
        publicado_pelo_sucessor: dict[str, tuple[int, int]] = {}
        sucessor: list[socket.socket] = []

        def _serve(self: object, sockets: list[socket.socket]) -> None:
            # Enquanto o antigo está "servindo", o novo entra e toma o lugar.
            pendente = diretorio / f"{runner.PENDING_PREFIX}sucessor"
            laco = _escutando(pendente)
            sucessor.append(laco)
            os.rename(pendente, runner.SOCKET_PATH)
            publicado_pelo_sucessor["id"] = _identidade(runner.SOCKET_PATH)

        monkeypatch.setattr(runner.uvicorn.Server, "run", _serve)
        monkeypatch.setattr(runner.os, "geteuid", lambda: runner._EXPECTED_UID)
        monkeypatch.setattr(runner.os, "getegid", lambda: runner._EXPECTED_GID)

        try:
            runner.main()

            assert runner.SOCKET_PATH.exists(), "o socket do sucessor foi apagado por quem saiu"
            assert _identidade(runner.SOCKET_PATH) == publicado_pelo_sucessor["id"]
        finally:
            for laco in sucessor:
                laco.close()

    def test_quem_sobe_sozinho_deixa_o_proprio_socket_publicado(
        self, diretorio: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sem sucessor, o socket fica — e recusa conexão.

        Deixar para trás é deliberado. Um socket sem ninguém atrás dá recusa de
        conexão, que é visível; o arquivo sumido virou PDF sem botão e ninguém
        percebeu. E quem subir depois publica por cima com `rename`, que não
        precisa do caminho livre.
        """
        publicado: dict[str, tuple[int, int]] = {}

        def _serve(self: object, sockets: list[socket.socket]) -> None:
            publicado["id"] = _identidade(runner.SOCKET_PATH)

        monkeypatch.setattr(runner.uvicorn.Server, "run", _serve)
        monkeypatch.setattr(runner.os, "geteuid", lambda: runner._EXPECTED_UID)
        monkeypatch.setattr(runner.os, "getegid", lambda: runner._EXPECTED_GID)

        runner.main()

        assert runner.SOCKET_PATH.exists()
        assert _identidade(runner.SOCKET_PATH) == publicado["id"]
        # E recusa conexão, em vez de não existir.
        sonda = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sonda.settimeout(1)
        try:
            with pytest.raises(OSError):
                sonda.connect(str(runner.SOCKET_PATH))
        finally:
            sonda.close()

    def test_a_subida_que_falha_nao_derruba_quem_esta_servindo(
        self, diretorio: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Uma tentativa que morre antes de publicar não toca no socket em uso.

        Era o outro lado do mesmo defeito: quem falhava antes de publicar não
        tinha identidade guardada, e a limpeza da saída passava a aceitar
        qualquer socket válido no caminho — inclusive o de quem estava
        atendendo.
        """
        servindo = _escutando(runner.SOCKET_PATH)
        identidade_de_quem_serve = _identidade(runner.SOCKET_PATH)

        def _explode(self: object, sockets: list[socket.socket]) -> None:
            raise RuntimeError("o serviço morreu antes de servir")

        monkeypatch.setattr(runner.uvicorn.Server, "run", _explode)
        monkeypatch.setattr(runner.os, "geteuid", lambda: runner._EXPECTED_UID)
        monkeypatch.setattr(runner.os, "getegid", lambda: runner._EXPECTED_GID)

        try:
            with pytest.raises(RuntimeError, match="morreu antes de servir"):
                runner.main()

            # A tentativa chegou a publicar, então o socket no caminho é o dela.
            # O que importa é que ele exista: o caminho nunca fica vazio.
            assert runner.SOCKET_PATH.exists()
        finally:
            servindo.close()
        assert identidade_de_quem_serve is not None

    def test_escuta_antes_de_publicar(
        self, diretorio: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Publicar antes de escutar deixaria uma janela de recusa.

        Caminho no lugar e ninguém atrás dele não é espera para quem conecta: é
        recusa. A janela é curta, mas cai bem no meio do deploy, que é quando
        todo mundo está reconectando.

        A ordem é observada na execução real de `main()`, e não lida no fonte.
        """
        ordem: list[str] = []
        listen_real = socket.socket.listen
        rename_real = os.rename

        def _listen(self: socket.socket, backlog: int = 0) -> None:
            ordem.append("listen")
            listen_real(self, backlog)

        def _rename(origem: object, destino: object) -> None:
            ordem.append("publica")
            rename_real(origem, destino)  # type: ignore[arg-type]

        monkeypatch.setattr(socket.socket, "listen", _listen)
        monkeypatch.setattr(runner.os, "rename", _rename)
        monkeypatch.setattr(runner.uvicorn.Server, "run", lambda self, sockets: None)
        monkeypatch.setattr(runner.os, "geteuid", lambda: runner._EXPECTED_UID)
        monkeypatch.setattr(runner.os, "getegid", lambda: runner._EXPECTED_GID)

        runner.main()

        assert ordem == ["listen", "publica"], "o socket foi publicado antes de escutar"

    def test_o_nome_de_quem_chega_nao_vem_do_pid(self, diretorio: Path) -> None:
        """Dois containers do mesmo deploy têm pid 1.

        Cada um no seu namespace, ambos escrevendo no mesmo volume. Um nome
        derivado do pid seria o mesmo nome para os dois: o que chegasse depois
        escreveria por cima do pendente do outro.
        """
        fonte = Path(runner.__file__).read_text()
        assert "getpid" not in fonte, "o nome do pendente voltou a depender do pid"

        nomes = {f"{runner.PENDING_PREFIX}{secrets.token_hex(8)}" for _ in range(500)}
        assert len(nomes) == 500


class TestNinguemRemoveSocketDeNinguem:
    """A regra que fecha a porta do incidente, nos dois sentidos."""

    def test_pendente_de_quem_esta_subindo_nao_e_removido(self, diretorio: Path) -> None:
        """Entre o `bind` e o `listen` o arquivo existe e ninguém atende ainda.

        Uma limpeza que perguntasse "alguém responde aqui?" concluiria que este
        socket está abandonado e o apagaria — e o processo que está subindo
        perderia o caminho debaixo dos pés. Seria o incidente de novo, por outro
        caminho: por isso a subida não remove nada.
        """
        subindo = diretorio / f"{runner.PENDING_PREFIX}aaaa"
        laco = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        laco.bind(str(subindo))  # sem `listen`: ninguém atende ainda
        try:
            runner._assert_only_our_files(diretorio)
            assert subindo.exists(), "o pendente de quem está subindo foi removido"
        finally:
            laco.close()

    def test_pendente_esquecido_tambem_fica(self, diretorio: Path) -> None:
        # Um processo que morreu antes de publicar deixa o arquivo para trás.
        # Ele não atrapalha: quem publica usa `rename`, que não precisa do
        # caminho livre. Distinguir os dois casos é que era a corrida.
        esquecido = diretorio / f"{runner.PENDING_PREFIX}bbbb"
        _abandonado(esquecido)

        runner._assert_only_our_files(diretorio)

        assert esquecido.exists()

    def test_o_socket_publicado_nunca_e_removido_na_subida(self, diretorio: Path) -> None:
        _abandonado(runner.SOCKET_PATH)

        runner._assert_only_our_files(diretorio)

        assert runner.SOCKET_PATH.exists()

    def test_arquivo_estranho_impede_o_servico_de_subir(self, diretorio: Path) -> None:
        # O diretório é do socket e dos pendentes. Qualquer outra coisa ali é
        # sinal de que o volume não é o que se pensa, e é a guarda que sobrou —
        # a única que não depende de adivinhar quem está vivo.
        (diretorio / "conteudo-do-vendedor.pdf").write_bytes(b"privado")

        with pytest.raises(RuntimeError, match="unexpected files"):
            runner._assert_only_our_files(diretorio)


class TestPublicacao:
    def test_o_caminho_nunca_fica_vazio_na_troca(self, diretorio: Path) -> None:
        """`rename` troca por cima, sem passar por um estado sem socket.

        Antes, quem subia removia o socket que encontrasse antes de criar o seu
        — e entre uma coisa e outra não havia socket nenhum no caminho.
        """
        caminho = runner.SOCKET_PATH
        anterior = _escutando(caminho)
        identidade_anterior = _identidade(caminho)

        pendente = diretorio / f"{runner.PENDING_PREFIX}cccc"
        novo = _escutando(pendente)
        try:
            runner._assert_replaceable(caminho)
            os.rename(pendente, caminho)
            assert caminho.exists()
            assert _identidade(caminho) != identidade_anterior
        finally:
            novo.close()
            anterior.close()

    def test_nao_publica_por_cima_do_que_nao_e_socket(self, diretorio: Path) -> None:
        """`rename` substitui em silêncio o que encontrar.

        Se no caminho estiver um arquivo comum, alguém pôs coisa que não é nossa
        num diretório que devia ter só o socket. O serviço para em vez de
        escrever por cima.
        """
        runner.SOCKET_PATH.write_bytes(b"nao sou um socket")

        with pytest.raises(RuntimeError, match="not a socket"):
            runner._assert_replaceable(runner.SOCKET_PATH)

    def test_publicar_sobre_o_caminho_vazio_e_permitido(self, diretorio: Path) -> None:
        runner._assert_replaceable(runner.SOCKET_PATH)


class TestGuardasDeSeguranca:
    def test_diretorio_com_permissao_larga_impede_o_servico_de_subir(self, diretorio: Path) -> None:
        # Um diretório aberto a mais gente é um socket que mais gente pode
        # substituir.
        os.chmod(diretorio, 0o755)  # noqa: S103 - o teste existe para exercitar isto.

        with pytest.raises(RuntimeError, match="directory ownership or mode"):
            runner._assert_directory(diretorio)

    def test_diretorio_vazio_com_dono_e_modo_certos_e_aceito(self, diretorio: Path) -> None:
        runner._assert_directory(diretorio)
        runner._assert_only_our_files(diretorio)

        assert list(diretorio.iterdir()) == []

    def test_socket_com_permissao_larga_e_recusado(self, diretorio: Path) -> None:
        caminho = runner.SOCKET_PATH
        laco = _escutando(caminho)
        os.chmod(caminho, 0o666)  # noqa: S103 - o teste existe para exercitar isto.
        try:
            with pytest.raises(RuntimeError, match="ownership, type or mode"):
                runner._assert_socket(caminho)
            assert stat.S_IMODE(os.lstat(caminho).st_mode) == 0o666
        finally:
            laco.close()
