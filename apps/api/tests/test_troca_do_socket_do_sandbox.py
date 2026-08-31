"""A troca do socket quando dois processos do serviço isolado convivem.

Todo deploy que sobe o novo antes de derrubar o antigo tem alguns segundos com
dois processos enxergando o mesmo diretório. O desenho anterior não previa isso:
quem entrava apagava o socket de quem já estava servindo, e quem saía apagava o
socket de quem tinha acabado de chegar.

O segundo caso derrubou a produção em 30/08/2026, com um único flag num comando
de deploy (`--update-order start-first`). O diretório ficou vazio, a API passou a
receber "arquivo não encontrado", a leitura de links virou `PdfLinksUnavailable`
e **todo PDF passou a abrir sem botão nenhum** — sem erro na tela e sem nada nos
logs. O `healthz` continuou dizendo que estava tudo bem, porque ele não prova o
socket.

Estes testes existem porque nada mais acusa: o serviço sobe, responde que está
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


def _abre(caminho: Path) -> socket.socket:
    """Cria um socket no caminho, como o serviço faz."""
    laco = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    laco.bind(str(caminho))
    os.chmod(caminho, 0o660)
    return laco


def _identidade(caminho: Path) -> tuple[int, int]:
    marca = os.lstat(caminho)
    return (marca.st_dev, marca.st_ino)


def test_o_processo_que_sai_nao_apaga_o_socket_do_sucessor(diretorio: Path) -> None:
    """O incidente, reproduzido.

    O antigo criou o socket; o novo tomou o lugar com o dele. Quando o antigo
    encerra, o socket que fica no caminho é do novo — e tem de continuar lá.
    """
    caminho = runner.SOCKET_PATH
    antigo = _abre(caminho)
    identidade_do_antigo = _identidade(caminho)

    # O novo entra: cria o seu com nome próprio e o põe no lugar.
    pendente = diretorio / f"{runner.PENDING_PREFIX}999"
    novo = _abre(pendente)
    os.chmod(pendente, 0o660)
    os.rename(pendente, caminho)
    identidade_do_novo = _identidade(caminho)
    assert identidade_do_novo != identidade_do_antigo

    # E agora o antigo encerra.
    antigo.close()
    runner._discard_if_mine(caminho, identidade=identidade_do_antigo)

    assert caminho.exists(), "o socket do sucessor foi apagado por quem saiu"
    assert _identidade(caminho) == identidade_do_novo
    novo.close()


def test_o_processo_que_sai_apaga_o_proprio_socket(diretorio: Path) -> None:
    # O outro lado: quando ninguém tomou o lugar, o socket sai junto com quem o
    # criou. Deixá-lo para trás faria a API tentar falar com um endereço morto.
    caminho = runner.SOCKET_PATH
    meu = _abre(caminho)
    identidade = _identidade(caminho)
    meu.close()

    runner._discard_if_mine(caminho, identidade=identidade)

    assert not caminho.exists()


def test_a_entrada_nao_derruba_quem_esta_servindo(diretorio: Path) -> None:
    """Quem chega não apaga o socket de quem está atendendo.

    Antes, a preparação removia o socket existente antes de criar o seu — e
    entre uma coisa e outra não havia socket nenhum no caminho. Com `rename`, o
    caminho nunca fica vazio.
    """
    caminho = runner.SOCKET_PATH
    servindo = _abre(caminho)
    identidade_de_quem_serve = _identidade(caminho)

    runner._discard_abandoned_sockets(diretorio)

    assert caminho.exists()
    assert _identidade(caminho) == identidade_de_quem_serve
    servindo.close()


def test_temporario_orfao_e_limpo_sem_tocar_no_socket(diretorio: Path) -> None:
    # Um `bind` que falhou deixa para trás o arquivo com o pid de quem tentou.
    # Ele sai; o socket em uso fica.
    caminho = runner.SOCKET_PATH
    servindo = _abre(caminho)
    orfao = diretorio / f"{runner.PENDING_PREFIX}4242"
    perdido = _abre(orfao)
    perdido.close()

    runner._discard_abandoned_sockets(diretorio)

    assert not orfao.exists()
    assert caminho.exists()
    servindo.close()


def test_arquivo_estranho_no_diretorio_e_recusado(diretorio: Path) -> None:
    # O diretório é do socket e de mais nada. Qualquer outra coisa ali é sinal
    # de que o volume não é o que se pensa, e o serviço não sobe em cima disso.
    (diretorio / "intruso").write_text("x")

    with pytest.raises(RuntimeError, match="unexpected files"):
        runner._discard_abandoned_sockets(diretorio)


def test_socket_com_permissao_larga_nao_e_apagado_as_cegas(diretorio: Path) -> None:
    """A remoção confere o que está removendo.

    Se o que está no caminho não é um socket nosso, com o dono e o modo certos,
    o serviço não o apaga: seria remover um arquivo de origem desconhecida num
    diretório compartilhado.
    """
    caminho = runner.SOCKET_PATH
    meu = _abre(caminho)
    identidade = _identidade(caminho)
    os.chmod(caminho, 0o666)  # noqa: S103 - o teste existe para exercitar isto.
    meu.close()

    runner._discard_if_mine(caminho, identidade=identidade)

    assert caminho.exists()
    assert stat.S_IMODE(os.lstat(caminho).st_mode) == 0o666


def test_diretorio_com_permissao_larga_impede_o_servico_de_subir(diretorio: Path) -> None:
    # Um diretório aberto a mais gente é um socket que mais gente pode
    # substituir. O serviço recusa em vez de servir de um lugar assim.
    os.chmod(diretorio, 0o755)  # noqa: S103 - o teste existe para exercitar isto.

    with pytest.raises(RuntimeError, match="directory ownership or mode"):
        runner._assert_directory(diretorio)


def test_diretorio_vazio_com_dono_e_modo_certos_e_aceito(diretorio: Path) -> None:
    # Vindo de `test_pdf_sandbox_runner.py`: o caso normal da primeira subida,
    # com o volume ainda vazio.
    runner._assert_directory(diretorio)
    runner._discard_abandoned_sockets(diretorio)

    assert list(diretorio.iterdir()) == []


def test_o_socket_anterior_nao_e_removido_na_entrada(diretorio: Path) -> None:
    """O contrato mudou aqui, e de propósito.

    Antes, quem subia removia o socket que encontrasse — inclusive o de um
    processo que ainda estava atendendo, e entre a remoção e a criação do novo
    não havia socket nenhum no caminho. Agora quem sobe cria o seu com nome
    próprio e o coloca no lugar com `rename`, que troca sem deixar buraco.

    Este teste substitui `test_prepare_removes_only_a_valid_stale_socket`, que
    guardava o comportamento antigo.
    """
    caminho = runner.SOCKET_PATH
    anterior = _abre(caminho)
    anterior.close()
    identidade_anterior = _identidade(caminho)

    runner._discard_abandoned_sockets(diretorio)
    assert caminho.exists(), "a entrada não pode remover o socket que encontrou"

    # E a troca acontece por cima, sem o caminho ficar vazio em momento nenhum.
    pendente = diretorio / f"{runner.PENDING_PREFIX}{os.getpid()}"
    novo = _abre(pendente)
    os.rename(pendente, caminho)
    try:
        assert _identidade(caminho) != identidade_anterior
    finally:
        novo.close()
