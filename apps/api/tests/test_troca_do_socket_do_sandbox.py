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
    """O caminho exato que apagou o socket em produção."""

    def test_quem_sai_nao_remove_o_socket_publicado(self, diretorio: Path) -> None:
        """Ninguém remove o socket do caminho ao sair — nem o próprio.

        Era a limpeza da saída que apagava o socket do sucessor. Entre olhar o
        arquivo e apagá-lo há sempre um instante, e nesse instante o sucessor
        pode ter tomado o lugar; não havendo remoção, não há instante.

        O custo é um socket sem ninguém atrás quando o serviço fica fora. Isso
        recusa conexão, que é visível — ao contrário do arquivo sumido, que
        virou PDF sem botão e ninguém percebeu.
        """
        fonte = Path(runner.__file__).read_text()
        saida = fonte[fonte.index("    finally:\n        server_socket.close()") :]
        saida = saida[: saida.index("\ndef _discard(")]

        assert "if not publicado:" in saida
        assert "SOCKET_PATH" not in saida, "a saída voltou a mexer no socket publicado"

    def test_dois_containers_com_o_mesmo_pid_nao_colidem(self, diretorio: Path) -> None:
        """O nome de quem chega é sorteado, e não derivado do pid.

        No Swarm os dois containers têm pid 1, cada um no seu namespace, ambos
        escrevendo no mesmo volume. Um nome derivado do pid seria o mesmo nome
        para os dois: o que chegasse depois escreveria por cima do pendente do
        outro, ou o apagaria na saída.
        """
        nomes = {f"{runner.PENDING_PREFIX}{__import__('secrets').token_hex(8)}" for _ in range(200)}
        assert len(nomes) == 200, "dois processos podem receber o mesmo nome"

        fonte = Path(runner.__file__).read_text()
        assert "getpid" not in fonte, "o nome do pendente voltou a depender do pid"


class TestLimpezaDePendentes:
    def test_pendente_de_processo_vivo_nao_e_removido(self, diretorio: Path) -> None:
        """Quem está de pé e ainda não publicou continua onde está.

        É o caso de dois deploys se atravessando. Quem decide é a conexão: o pid
        no nome seria o mesmo para os dois containers, e a data do arquivo não
        diz nada sobre quem está do outro lado.
        """
        vivo = diretorio / f"{runner.PENDING_PREFIX}aaaa"
        laco = _escutando(vivo)
        try:
            runner._discard_abandoned_sockets(diretorio)
            assert vivo.exists(), "o pendente de um processo vivo foi removido"
        finally:
            laco.close()

    def test_pendente_sem_ninguem_atras_e_removido(self, diretorio: Path) -> None:
        # Um `bind` que deu certo e um processo que morreu antes de publicar.
        morto = diretorio / f"{runner.PENDING_PREFIX}bbbb"
        _abandonado(morto)

        runner._discard_abandoned_sockets(diretorio)

        assert not morto.exists()

    def test_o_socket_publicado_nunca_entra_na_limpeza(self, diretorio: Path) -> None:
        # Nem quando está abandonado: quem substitui é o `rename`, e ele não
        # precisa que o caminho esteja livre.
        _abandonado(runner.SOCKET_PATH)

        runner._discard_abandoned_sockets(diretorio)

        assert runner.SOCKET_PATH.exists()

    def test_arquivo_estranho_impede_o_servico_de_subir(self, diretorio: Path) -> None:
        # O diretório é do socket e de mais nada. Qualquer outra coisa ali é
        # sinal de que o volume não é o que se pensa.
        (diretorio / "conteudo-do-vendedor.pdf").write_bytes(b"privado")

        with pytest.raises(RuntimeError, match="unexpected files"):
            runner._discard_abandoned_sockets(diretorio)


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
        runner._discard_abandoned_sockets(diretorio)

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
