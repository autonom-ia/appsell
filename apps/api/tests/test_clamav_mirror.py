"""O que se lê do mirror vem da rede, e é tratado como tal."""

from __future__ import annotations

import struct

import pytest

from appsell_api import clamav_mirror


def _resposta_txt(pedido_id: int, texto: str, *, tipo: int = 16) -> bytes:
    """Monta uma resposta de DNS com um TXT, como a do mirror."""
    cabecalho = struct.pack("!HHHHHH", pedido_id, 0x8180, 1, 1, 0, 0)
    nome = b"".join(bytes([len(p)]) + p for p in clamav_mirror._MIRROR.encode("ascii").split(b"."))
    pergunta = nome + b"\x00" + struct.pack("!HH", 16, 1)
    corpo = bytes([len(texto)]) + texto.encode("ascii")
    # 0xC00C é o ponteiro de compressão para o nome já escrito na pergunta —
    # é assim que um servidor de verdade responde, e o leitor precisa andar
    # por cima dele sem se perder.
    resposta = b"\xc0\x0c" + struct.pack("!HHIH", tipo, 1, 60, len(corpo)) + corpo
    return cabecalho + pergunta + resposta


def test_le_a_base_diaria_do_txt_do_mirror(monkeypatch: pytest.MonkeyPatch) -> None:
    """O formato real, colhido do mirror em 12/09/2026."""
    _responde(
        monkeypatch, lambda pid: _resposta_txt(pid, "1.4.6:63:28121:1789203600:1:90:49192:339")
    )

    assert clamav_mirror.daily_publicada_no_mirror() == 28121


def test_resposta_de_outro_pedido_e_descartada(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resposta com id que não é o da pergunta não é resposta nossa.

    Sem esta checagem, qualquer pacote UDP que chegasse na porta viraria
    veredito sobre o antivírus.
    """
    _responde(monkeypatch, lambda pid: _resposta_txt(pid + 1, "1.4.6:63:99999:0:1:90:1:1"))

    assert clamav_mirror.daily_publicada_no_mirror() is None


@pytest.mark.parametrize(
    "texto",
    [
        "",
        "sem-dois-pontos",
        "1.4.6:63",
        "1.4.6:63:nao-e-numero:0",
        "1.4.6:63:0:0",
        "1.4.6:63:-5:0",
    ],
)
def test_texto_que_nao_traz_a_base_vira_nao_sei(
    monkeypatch: pytest.MonkeyPatch, texto: str
) -> None:
    """Nada aqui pode levantar exceção nem inventar um número."""
    _responde(monkeypatch, lambda pid: _resposta_txt(pid, texto))

    assert clamav_mirror.daily_publicada_no_mirror() is None


def test_resposta_truncada_nao_quebra(monkeypatch: pytest.MonkeyPatch) -> None:
    _responde(monkeypatch, lambda pid: _resposta_txt(pid, "1.4.6:63:28121:0:1:90:1:1")[:20])

    assert clamav_mirror.daily_publicada_no_mirror() is None


def test_registro_que_nao_e_txt_e_ignorado(monkeypatch: pytest.MonkeyPatch) -> None:
    _responde(monkeypatch, lambda pid: _resposta_txt(pid, "1.4.6:63:28121:0:1:90:1:1", tipo=1))

    assert clamav_mirror.daily_publicada_no_mirror() is None


def test_rede_fora_vira_nao_sei_em_vez_de_excecao(monkeypatch: pytest.MonkeyPatch) -> None:
    """`None` é "não sei", e quem chama nunca o lê como "estou desatualizado"."""
    monkeypatch.setattr(clamav_mirror, "_nameservers", lambda: ["10.0.0.1"])

    class _SocketMudo:
        def __enter__(self) -> _SocketMudo:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def settimeout(self, _v: float) -> None:
            pass

        def sendto(self, _d: bytes, _a: tuple[str, int]) -> None:
            raise OSError("rede fora")

        def recvfrom(self, _n: int) -> tuple[bytes, object]:
            raise AssertionError("não deveria chegar aqui")

    monkeypatch.setattr("appsell_api.clamav_mirror.socket.socket", lambda *_a: _SocketMudo())

    assert clamav_mirror.daily_publicada_no_mirror() is None


def test_sem_resolvedor_configurado_nao_consulta_ninguem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(clamav_mirror, "_nameservers", list)

    assert clamav_mirror.daily_publicada_no_mirror() is None


def _responde(monkeypatch: pytest.MonkeyPatch, monta) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(clamav_mirror, "_nameservers", lambda: ["10.0.0.1"])
    enviados: list[int] = []

    class _SocketFalso:
        def __enter__(self) -> _SocketFalso:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def settimeout(self, _v: float) -> None:
            pass

        def sendto(self, dados: bytes, _a: tuple[str, int]) -> None:
            enviados.append(struct.unpack("!H", dados[:2])[0])

        def recvfrom(self, _n: int) -> tuple[bytes, object]:
            return monta(enviados[-1]), ("10.0.0.1", 53)

    monkeypatch.setattr("appsell_api.clamav_mirror.socket.socket", lambda *_a: _SocketFalso())
