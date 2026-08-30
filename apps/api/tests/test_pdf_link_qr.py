"""O botão sem destino recebe o endereço do QR do seu próprio cartão."""

from __future__ import annotations

from pathlib import Path

import pytest
import zxingcpp
from PIL import Image

from appsell_api.pdf_link_qr import (
    MAX_RENDER_PIXELS,
    MAX_VERTICAL_DISTANCE,
    QrBudget,
    QrTarget,
    _center,
    _decode,
    assign_targets,
    read_page_qr_targets,
    render_arguments,
)

ALVO = "https://midia.example.test/aula.mov"


class _Canto:
    def __init__(self, y: float) -> None:
        self.y = y
        self.x = 0.0


class _Posicao:
    def __init__(self, topo: float, base: float) -> None:
        self.top_left = _Canto(topo)
        self.top_right = _Canto(topo)
        self.bottom_right = _Canto(base)
        self.bottom_left = _Canto(base)


class TestCenter:
    def test_reads_the_middle_of_the_code(self) -> None:
        assert _center(_Posicao(100, 300), 1000, "y") == pytest.approx(0.2)

    def test_reads_the_horizontal_middle_too(self) -> None:
        posicao = _Posicao(0, 0)
        for canto in ("top_left", "top_right", "bottom_right", "bottom_left"):
            getattr(posicao, canto).x = 400.0
        assert _center(posicao, 800, "x") == pytest.approx(0.5)

    def test_a_position_without_corners_is_ignored(self) -> None:
        assert _center(None, 1000, "y") is None
        assert _center(_Posicao(float("nan"), float("nan")), 1000, "y") is None


class TestMatching:
    """Qual QR pertence a qual botão."""

    def test_the_nearest_code_on_the_page_wins(self) -> None:
        alvos = [
            QrTarget("https://a.test/um.mov", 0.30, 0.8),
            QrTarget("https://a.test/dois.mov", 0.60, 0.8),
        ]
        assert assign_targets(alvos, [(0.31, 0.2), (0.58, 0.2)]) == [
            "https://a.test/um.mov",
            "https://a.test/dois.mov",
        ]

    def test_two_columns_on_the_same_row_are_told_apart(self) -> None:
        # Dois cartões lado a lado, na mesma altura. Só a vertical daria o mesmo
        # QR aos dois botões, e um dos alunos iria para o vídeo errado.
        alvos = [
            QrTarget("https://a.test/esquerda.mov", 0.40, 0.30),
            QrTarget("https://a.test/direita.mov", 0.40, 0.80),
        ]
        assert assign_targets(alvos, [(0.40, 0.10), (0.40, 0.60)]) == [
            "https://a.test/esquerda.mov",
            "https://a.test/direita.mov",
        ]

    def test_one_code_is_never_given_to_two_buttons(self) -> None:
        # Dois botões, um QR só decodificado. O segundo fica sem destino: dar o
        # mesmo endereço aos dois mandaria um dos alunos para o vídeo errado, e
        # errado em silêncio.
        alvos = [QrTarget(ALVO, 0.40, 0.85)]
        assert assign_targets(alvos, [(0.40, 0.2), (0.42, 0.2)]) == [ALVO, None]

    def test_height_decides_even_when_width_disagrees(self) -> None:
        # Um QR 4% mais distante na vertical não pode ganhar por estar mais
        # perto na horizontal: a altura é o que separa os cartões.
        alvos = [
            QrTarget("https://a.test/certo.mov", 0.40, 0.85),
            QrTarget("https://a.test/errado.mov", 0.44, 0.20),
        ]
        assert assign_targets(alvos, [(0.40, 0.20)]) == ["https://a.test/certo.mov"]

    def test_an_ambiguous_layout_gets_no_target(self) -> None:
        alvos = [
            QrTarget("https://a.test/um.mov", 0.40, 0.48),
            QrTarget("https://a.test/dois.mov", 0.40, 0.52),
        ]
        assert assign_targets(alvos, [(0.40, 0.50)]) == [None]

    def test_a_distant_code_is_not_borrowed(self) -> None:
        alvos = [QrTarget(ALVO, 0.10, 0.5)]
        assert assign_targets(alvos, [(0.10 + MAX_VERTICAL_DISTANCE + 0.01, 0.5)]) == [None]

    def test_a_page_without_codes_gives_nothing(self) -> None:
        assert assign_targets([], [(0.5, 0.5)]) == [None]


class TestRenderArguments:
    """O comando da rasterização carrega os limites que dizemos ter."""

    def test_limits_the_image_and_uses_the_displayed_box(self, tmp_path: Path) -> None:
        argv = render_arguments(tmp_path / "a.pdf", 3, tmp_path / "p")
        assert "-scale-to" in argv and str(MAX_RENDER_PIXELS) in argv
        # Sem `-cropbox`, um `CropBox` diferente do `MediaBox` desloca tudo e o
        # botão casa com o QR errado.
        assert "-cropbox" in argv
        assert argv[argv.index("-f") + 1] == "3"
        assert argv[argv.index("-l") + 1] == "3"


class TestBudget:
    """O orçamento é do documento, e não de cada página."""

    def test_an_exhausted_budget_renders_nothing(self, tmp_path: Path) -> None:
        # Sem isto, um PDF com um botão sem destino por página dispararia uma
        # rasterização por página e seguraria o serviço por dezenas de minutos.
        pdf = tmp_path / "a.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        assert read_page_qr_targets(pdf, 1, QrBudget(0.0)) == []

    def test_a_page_outside_the_ceiling_is_not_rendered(self, tmp_path: Path) -> None:
        pdf = tmp_path / "a.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        assert read_page_qr_targets(pdf, 100_000, QrBudget(30.0)) == []


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
            "https://usuario:senha@midia.example.test/aula.mov",
            "https://midia.example.test:8443/aula.mov",
            "https://midia.example.test\\@outro.test/aula.mov",
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
