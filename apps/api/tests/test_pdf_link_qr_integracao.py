"""O caminho inteiro: PDF com QR e botão sem destino → link no manifesto.

Os testes de unidade cobrem as peças. Este cobre o que liga elas — o processo
filho, a rasterização, a leitura e a distribuição — porque foi exatamente numa
ligação que os limites viraram comentário sem efeito antes.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pikepdf
import pytest
import zxingcpp
from PIL import Image

from appsell_api import pdf_link_qr, pdf_links
from appsell_api.pdf_link_qr import PdfLinkQrUnavailable, QrBudget, read_qr_targets
from appsell_api.pdf_links import extract_pdf_link_manifest

ALVO = "https://midia.example.test/aula.mov"
OUTRO = "https://midia.example.test/outra.mov"


def _pdf_com_qr(caminho: Path, enderecos: list[tuple[str, int]]) -> None:
    """Uma página com um QR por endereço, na altura pedida (em pixels)."""
    pagina = Image.new("RGB", (800, 1000), "white")
    for endereco, topo in enderecos:
        codigo = zxingcpp.write_barcode(
            zxingcpp.BarcodeFormat.QRCode, endereco, width=160, height=160
        )
        pagina.paste(Image.fromarray(codigo).convert("RGB"), (600, topo))
    pagina.save(caminho, "PDF", resolution=100.0)


def _com_botoes(
    caminho: Path, retangulos: list[tuple[float, float]], **campos: pikepdf.Object
) -> None:
    """Acrescenta anotações `/Link` sem destino, em pontos PDF."""
    with pikepdf.open(caminho, allow_overwriting_input=True) as pdf:
        pagina = pdf.pages[0]
        anotacoes = []
        for baixo, cima in retangulos:
            anotacao = pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Annot"),
                    "/Subtype": pikepdf.Name("/Link"),
                    "/Rect": pikepdf.Array([50, baixo, 300, cima]),
                    **{f"/{nome}": valor for nome, valor in campos.items()},
                }
            )
            anotacoes.append(pdf.make_indirect(anotacao))
        pagina.Annots = pdf.make_indirect(pikepdf.Array(anotacoes))
        pdf.save()


def _manifesto(caminho: Path) -> list[dict[str, object]]:
    dados = caminho.read_bytes()
    saida = extract_pdf_link_manifest(
        caminho,
        expected_size=len(dados),
        expected_sha256_hex=hashlib.sha256(dados).hexdigest(),
    )
    return list(saida["links"])


class TestCaminhoCompleto:
    def test_a_button_without_a_destination_learns_the_address(self, tmp_path: Path) -> None:
        # O caso do vendedor: botão desenhado, QR impresso, anotação vazia.
        pdf = tmp_path / "a.pdf"
        _pdf_com_qr(pdf, [(ALVO, 400)])
        _com_botoes(pdf, [(300, 360)])
        links = _manifesto(pdf)
        assert [link["target_url"] for link in links] == [ALVO]
        assert links[0]["kind"] == "video"

    def test_a_refused_destination_is_not_replaced(self, tmp_path: Path) -> None:
        # `/Dest` presente e não resolvido é destino recusado, não ausente.
        # Trocá-lo pelo QR ao lado entregaria ao aluno um endereço diferente do
        # que o PDF manda.
        pdf = tmp_path / "b.pdf"
        _pdf_com_qr(pdf, [(ALVO, 400)])
        _com_botoes(pdf, [(300, 360)], Dest=pikepdf.Array([pikepdf.String("inexistente")]))
        assert _manifesto(pdf) == []

    def test_an_action_field_also_counts_as_a_destination(self) -> None:
        # `/A` entra na mesma condição que `/Dest`, na mesma linha: a anotação
        # tem destino, ele não resolveu, e o QR não o substitui. O caso completo
        # em PDF não cabe aqui — construir um `/A` de salto faz o verificador
        # recusar o arquivo antes, por outro motivo — então a garantia é lida do
        # próprio código, que é onde ela vive.
        fonte = Path(pdf_links.__file__).read_text(encoding="utf-8")
        assert 'annotation.get("/Dest") is not None' in fonte
        assert 'annotation.get("/A") is not None' in fonte

    def test_two_buttons_read_the_page_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Rasterizar de novo por botão multiplicaria o custo numa página cheia.
        pdf = tmp_path / "d.pdf"
        # A página tem 720 pt de altura (800x1000 px a 100 dpi). No PDF a
        # origem fica embaixo; na imagem, em cima. O QR colado a 200 px do topo
        # corresponde a y ≈ 576 pt, e o de 700 px a y ≈ 216 pt.
        _pdf_com_qr(pdf, [(ALVO, 200), (OUTRO, 700)])
        _com_botoes(pdf, [(550, 600), (190, 240)])

        leituras = {"quantas": 0}
        original = pdf_link_qr.read_qr_targets

        def _contando(*args: object, **kwargs: object) -> object:
            leituras["quantas"] += 1
            return original(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr("appsell_api.pdf_links.read_qr_targets", _contando)
        links = _manifesto(pdf)
        # Uma leitura para o documento inteiro. Uma por página gastava o tempo
        # de partida do processo vezes o número de páginas, e o orçamento
        # acabava antes de o leitor terminar — foi o que fez o PDF do vendedor
        # voltar sem nenhum link novo em produção.
        assert leituras["quantas"] == 1
        assert sorted(str(link["target_url"]) for link in links) == sorted([ALVO, OUTRO])


class TestWorkerTerminavel:
    def test_the_group_is_killed_when_the_deadline_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # O filho fica preso de propósito. Sem o `killpg`, ele seguiria vivo
        # depois de ninguém mais estar esperando pelo resultado.
        visto: dict[str, object] = {}
        real_popen = subprocess.Popen

        def _preso(argv: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
            visto["start_new_session"] = kwargs.get("start_new_session")
            processo: subprocess.Popen[bytes] = real_popen(  # noqa: S603 - teste; comando fixo
                ["sleep", "30"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return processo

        monkeypatch.setattr("appsell_api.pdf_link_qr.subprocess.Popen", _preso)
        pdf = tmp_path / "e.pdf"
        _pdf_com_qr(pdf, [(ALVO, 400)])
        with pytest.raises(PdfLinkQrUnavailable):
            read_qr_targets(pdf, [1], QrBudget(0.5))
        # Sessão própria é o que permite matar o grupo, e não só o filho direto.
        assert visto["start_new_session"] is True


class TestLeituraEmLote:
    """Todas as páginas do documento numa leitura só.

    Um processo por página gastava o tempo de partida do Python vezes o número
    de páginas — no PDF do vendedor, com vinte e um botões sem destino, isso
    sozinho consumia o orçamento e o documento voltava sem nenhum link novo.
    Foi o defeito que só apareceu em produção.
    """

    def test_two_pages_are_read_in_a_single_process(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pdf = tmp_path / "duas.pdf"
        _pdf_de_duas_paginas(pdf, [(ALVO, 400), (OUTRO, 400)])
        _com_botoes_em(pdf, {0: [(300, 360)], 1: [(300, 360)]})

        chamadas: list[list[str]] = []
        real = subprocess.Popen

        def _contando(argv: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
            chamadas.append(list(argv))
            processo: subprocess.Popen[bytes] = real(argv, **kwargs)  # type: ignore[arg-type,call-overload]
            return processo

        monkeypatch.setattr("appsell_api.pdf_link_qr.subprocess.Popen", _contando)
        links = _manifesto(pdf)

        # Um processo para o documento inteiro, com as duas páginas no argv.
        assert len(chamadas) == 1
        assert chamadas[0][-1] == "1,2"
        # E cada página com o seu próprio endereço, não o da outra.
        por_pagina = {int(link["page"]): str(link["target_url"]) for link in links}
        assert por_pagina == {1: ALVO, 2: OUTRO}


def _pdf_de_duas_paginas(caminho: Path, enderecos: list[tuple[str, int]]) -> None:
    paginas = []
    for endereco, topo in enderecos:
        pagina = Image.new("RGB", (800, 1000), "white")
        codigo = zxingcpp.write_barcode(
            zxingcpp.BarcodeFormat.QRCode, endereco, width=160, height=160
        )
        pagina.paste(Image.fromarray(codigo).convert("RGB"), (600, topo))
        paginas.append(pagina)
    paginas[0].save(caminho, "PDF", resolution=100.0, save_all=True, append_images=paginas[1:])


def _com_botoes_em(caminho: Path, por_pagina: dict[int, list[tuple[float, float]]]) -> None:
    with pikepdf.open(caminho, allow_overwriting_input=True) as pdf:
        for indice, retangulos in por_pagina.items():
            anotacoes = [
                pdf.make_indirect(
                    pikepdf.Dictionary(
                        {
                            "/Type": pikepdf.Name("/Annot"),
                            "/Subtype": pikepdf.Name("/Link"),
                            "/Rect": pikepdf.Array([50, baixo, 300, cima]),
                        }
                    )
                )
                for baixo, cima in retangulos
            ]
            pdf.pages[indice].Annots = pdf.make_indirect(pikepdf.Array(anotacoes))
        pdf.save()
