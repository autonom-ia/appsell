"""O botão sem destino recebe o endereço do QR do seu próprio cartão."""

from __future__ import annotations

from pathlib import Path

import pytest
import zxingcpp
from PIL import Image

from appsell_api.pdf_link_qr import (
    MAX_VERTICAL_DISTANCE,
    QrTarget,
    _decode,
    _vertical_center,
    target_for_rectangle,
)

ALVO = "https://midia.example.test/aula.mov"


class _Canto:
    def __init__(self, y: float) -> None:
        self.y = y


class _Posicao:
    def __init__(self, topo: float, base: float) -> None:
        self.top_left = _Canto(topo)
        self.top_right = _Canto(topo)
        self.bottom_right = _Canto(base)
        self.bottom_left = _Canto(base)


class TestVerticalCenter:
    def test_reads_the_middle_of_the_code(self) -> None:
        assert _vertical_center(_Posicao(100, 300), 1000) == pytest.approx(0.2)

    def test_a_position_without_corners_is_ignored(self) -> None:
        assert _vertical_center(None, 1000) is None
        assert _vertical_center(_Posicao(float("nan"), float("nan")), 1000) is None


class TestMatching:
    """Qual QR pertence a qual botão."""

    def test_the_nearest_code_on_the_page_wins(self) -> None:
        # Página com dois cartões: o botão de cima pega o QR de cima.
        alvos = [QrTarget("https://a.test/um.mov", 0.30), QrTarget("https://a.test/dois.mov", 0.60)]
        assert target_for_rectangle(alvos, 0.31) == "https://a.test/um.mov"
        assert target_for_rectangle(alvos, 0.58) == "https://a.test/dois.mov"

    def test_a_distant_code_is_not_borrowed(self) -> None:
        # Sem QR perto o bastante, o botão continua sem destino. Emprestar o de
        # outro cartão mandaria o aluno para o vídeo errado — pior que não abrir.
        alvos = [QrTarget(ALVO, 0.10)]
        assert target_for_rectangle(alvos, 0.10 + MAX_VERTICAL_DISTANCE + 0.01) is None

    def test_a_page_without_codes_gives_nothing(self) -> None:
        assert target_for_rectangle([], 0.5) is None


def _pagina_com_qr(caminho: Path, texto: str, topo_fracao: float) -> None:
    """Uma página branca com um QR na altura pedida."""
    codigo = zxingcpp.write_barcode(zxingcpp.BarcodeFormat.QRCode, texto, width=200, height=200)
    pagina = Image.new("RGB", (800, 1000), "white")
    pagina.paste(Image.fromarray(codigo).convert("RGB"), (300, int(1000 * topo_fracao)))
    pagina.save(caminho)


class TestDecode:
    """A leitura de verdade, de uma imagem de verdade."""

    def test_reads_an_https_address_and_where_it_is(self, tmp_path: Path) -> None:
        imagem = tmp_path / "pagina.png"
        _pagina_com_qr(imagem, ALVO, 0.40)
        alvos = _decode(imagem)
        assert [a.url for a in alvos] == [ALVO]
        # O centro do QR fica entre o topo onde foi colado e o fim dele.
        assert 0.40 < alvos[0].center < 0.62

    @pytest.mark.parametrize(
        "texto",
        [
            "http://midia.example.test/aula.mov",
            "tel:+5511999999999",
            "apenas um texto",
            "javascript:alert(1)",
        ],
    )
    def test_refuses_anything_that_is_not_a_secure_address(
        self, tmp_path: Path, texto: str
    ) -> None:
        # O QR é conteúdo de terceiro como qualquer outro. Só `https` vira link;
        # o resto não tem o que fazer no leitor do aluno.
        imagem = tmp_path / "pagina.png"
        _pagina_com_qr(imagem, texto, 0.40)
        assert _decode(imagem) == []

    def test_two_codes_on_one_page_are_read_separately(self, tmp_path: Path) -> None:
        codigo_um = zxingcpp.write_barcode(
            zxingcpp.BarcodeFormat.QRCode, "https://a.test/um.mov", width=200, height=200
        )
        codigo_dois = zxingcpp.write_barcode(
            zxingcpp.BarcodeFormat.QRCode, "https://a.test/dois.mov", width=200, height=200
        )
        pagina = Image.new("RGB", (800, 1000), "white")
        pagina.paste(Image.fromarray(codigo_um).convert("RGB"), (300, 100))
        pagina.paste(Image.fromarray(codigo_dois).convert("RGB"), (300, 700))
        imagem = tmp_path / "pagina.png"
        pagina.save(imagem)

        alvos = sorted(_decode(imagem), key=lambda a: a.center)
        assert [a.url for a in alvos] == ["https://a.test/um.mov", "https://a.test/dois.mov"]
        assert alvos[0].center < 0.5 < alvos[1].center

    def test_a_page_without_codes_reads_nothing(self, tmp_path: Path) -> None:
        imagem = tmp_path / "pagina.png"
        Image.new("RGB", (800, 1000), "white").save(imagem)
        assert _decode(imagem) == []
