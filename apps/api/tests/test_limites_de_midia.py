"""Os limites de mídia, e o fato de eles estarem escritos em vários lugares.

Cada valor aqui aparece em mais de uma camada: Python, TypeScript, restrições do
banco e funções PL/pgSQL. Já aconteceu de subir o número num lugar e deixar
outro para trás — o efeito não é o limite continuar valendo, é a recusa mudar de
lugar: sai do envio, onde a mensagem é clara, e vai para o `INSERT`, onde o
vendedor vê um erro de banco.

Estes testes existem para a próxima mudança de teto ter de passar por todas as
camadas, e não só pela primeira que alguém encontrar.
"""

from __future__ import annotations

import re
from pathlib import Path

from appsell_api import learning_pdf, pdf_derivatives, pdf_link_qr, pdf_link_video, video_remux
from appsell_api.asset_media import PDF_MEDIA_TYPE, VIDEO_MEDIA_TYPE, max_bytes

_RAIZ = Path(__file__).resolve().parents[3]
_MIB = 1024 * 1024


class TestTetoDePaginas:
    def test_o_leitor_e_o_pipeline_de_derivados_concordam(self) -> None:
        """Dois módulos, o mesmo documento.

        `learning_pdf` decide o que o aluno pode abrir; `pdf_derivatives` decide
        o que o worker aceita rasterizar. Se discordarem, existe um documento
        que é aceito no envio e recusado na leitura — ou o contrário.
        """
        assert learning_pdf.MAX_PDF_PAGES == pdf_derivatives.MAX_PDF_PAGES == 300

    def test_a_leitura_de_links_usa_a_mesma_constante(self) -> None:
        # Era um `100` escrito à mão, e foi ele que sobreviveu à última subida
        # de teto: o documento passava no envio e voltava sem link nenhum.
        fonte = Path(pdf_link_video.__file__).parent / "pdf_links.py"
        corpo = fonte.read_text()
        assert "expected_page_count <= MAX_PDF_PAGES" in corpo
        assert not re.search(r"expected_page_count <= \d+", corpo)

    def test_a_varredura_de_qr_alcanca_o_documento_inteiro(self) -> None:
        # Um teto menor que o do documento deixaria os botões das últimas
        # páginas sem destino, e ninguém saberia por quê. O custo continua
        # limitado pelo orçamento de tempo, não por este número.
        assert pdf_link_qr.MAX_QR_PAGES >= learning_pdf.MAX_PDF_PAGES

    def test_a_camada_web_carrega_o_mesmo_numero(self) -> None:
        """O navegador recusa antes de pedir, e precisa recusar no mesmo ponto.

        Um teto menor no cliente esconde documentos que o servidor aceitaria; um
        teto maior deixa o aluno pedir o que vai voltar como erro.
        """
        web = (_RAIZ / "lib/pdf-study.ts").read_text()
        encontrado = re.search(r"export const MAX_PDF_PAGES = (\d+);", web)
        assert encontrado is not None, "a camada web perdeu a constante"
        assert int(encontrado.group(1)) == learning_pdf.MAX_PDF_PAGES

    def test_nenhuma_camada_web_escreve_o_numero_a_mao(self) -> None:
        # As quatro cópias que existiam.
        for caminho in (
            "lib/internal-api-client.server.ts",
            "lib/protected-pdf-links.server.ts",
            "app/components/protected-pdf-reader.tsx",
        ):
            corpo = (_RAIZ / caminho).read_text()
            assert "MAX_PDF_PAGES" in corpo, caminho
            assert not re.search(r"page_?[Cc]ount\)? [<>]=? 100\b", corpo), caminho

    def test_a_migration_cobre_as_oito_marcas_do_banco(self) -> None:
        """Seis restrições e duas funções carregavam o número.

        Enquanto o banco disser 100, subir o teto no código só muda onde a
        recusa acontece. Foi assim que a última subida ficou pela metade.
        """
        migration = next(
            p
            for p in (_RAIZ / "apps/api/migrations/versions").iterdir()
            if p.name.startswith("0044_")
        ).read_text()
        for restricao in (
            "asset_pdf_raster_page_count_check",
            "asset_pdf_page_page_number_check",
            "asset_pdf_raster_candidate_page_count_check",
            "asset_pdf_page_candidate_page_number_check",
            "learning_pdf_annotation_page_number_check",
            "learning_pdf_bookmark_page_number_check",
        ):
            assert restricao in migration, restricao
        for funcao in ("authorize_entitled_pdf_page", "register_asset_ready_candidate_v2"):
            assert funcao in migration, funcao
        assert "300" in migration


class TestTetoDoPacoteRasterizado:
    def test_o_pacote_cabe_o_documento_no_pior_caso_medido(self) -> None:
        """384 MiB, e o número vem de medição.

        Medido no playbook do vendedor: 243 KiB por página em média, 642 KiB na
        página mais pesada. Trezentas páginas do pior caso são 193 MiB — o teto
        é o dobro disso. O anterior, de 128 MiB, recusaria.
        """
        pior_caso_por_pagina = 642 * 1024
        necessario = learning_pdf.MAX_PDF_PAGES * pior_caso_por_pagina
        teto_novo = 384 * _MIB
        assert teto_novo >= necessario * 2
        assert teto_novo <= pdf_derivatives.MAX_PDF_DERIVATIVE_BYTES


class TestTetoDeVideo:
    def test_o_video_do_pdf_usa_o_teto_do_catalogo(self) -> None:
        """O mesmo conteúdo tinha dois tetos, cinco vezes distantes.

        O vídeo enviado pelo vendedor sempre valeu 100 MiB, pelo catálogo. O
        citado dentro do PDF valia 512, número que nasceu no módulo de vídeo sem
        olhar o catálogo.
        """
        do_catalogo = max_bytes(VIDEO_MEDIA_TYPE)
        assert pdf_link_video.MAX_SOURCE_BYTES == do_catalogo
        assert pdf_link_video.MAX_DERIVATIVE_BYTES == do_catalogo
        assert video_remux.MAX_DERIVATIVE_BYTES == do_catalogo

    def test_o_bff_tambem_usa_o_teto_do_catalogo(self) -> None:
        rota = _RAIZ / (
            "app/api/library/items/[entitlementId]/assets/[assetId]"
            "/pages/links/[linkId]/media/route.ts"
        )
        corpo = rota.read_text()
        assert "MAX_ASSET_VIDEO_BYTES" in corpo
        assert not re.search(r"MAX_VIDEO_BYTES = \d+ \*", corpo)

    def test_a_duracao_cabe_no_prazo_da_conversao(self) -> None:
        """O teto de duração tem de caber no prazo, ou promete o que não entrega.

        Medido no serviço isolado: 66 segundos de conversão para cada minuto de
        1080p, com as duas threads que ele tem. Quatro horas levariam quatro e
        meia, e o prazo é de uma — o vídeo era aceito e nunca ficava pronto.
        """
        segundos_por_segundo_de_video = 66 / 60
        pior_conversao = video_remux.MAX_DURATION_SECONDS * segundos_por_segundo_de_video
        assert pior_conversao < video_remux.TRANSCODE_TIMEOUT_SECONDS

    def test_o_pdf_continua_com_o_teto_do_catalogo(self) -> None:
        # Não mudou, e o teste existe para continuar não mudando por acidente.
        from appsell_api.pdf_verifier import MAX_PDF_BYTES

        assert MAX_PDF_BYTES == max_bytes(PDF_MEDIA_TYPE) == 100 * _MIB
