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

import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest

from appsell_api import learning_pdf, pdf_derivatives, pdf_link_qr

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
        fonte = Path(pdf_derivatives.__file__).parent / "pdf_links.py"
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

    def test_nenhum_arquivo_da_web_escreve_o_numero_a_mao(self) -> None:
        """Varre a web inteira, e não a lista de arquivos de que eu me lembrei.

        A versão anterior deste teste citava três arquivos e procurava por
        `page_count`. O ponto que escapou foi um quarto arquivo, com outro
        formato: `safeInteger(page, 1, 100)` no BFF — que recusava a página 101
        antes mesmo de perguntar à API.

        Listar onde procurar é procurar o que já se sabe. Aqui a busca é por
        qualquer número cru ao lado de uma página, em qualquer arquivo.
        """
        suspeito = re.compile(r"\bpage(?:_count|Count|_number|Number)?\b[^\n]{0,40}?\b(\d{2,4})\b")
        achados: list[str] = []
        for pasta in ("lib", "app"):
            for caminho in sorted((_RAIZ / pasta).rglob("*.ts*")):
                if "node_modules" in caminho.parts or caminho.name.endswith(".d.ts"):
                    continue
                for numero, linha in enumerate(caminho.read_text().splitlines(), 1):
                    if "MAX_PDF_PAGES" in linha or linha.lstrip().startswith(("*", "//")):
                        continue
                    for encontrado in suspeito.finditer(linha):
                        # Só interessa o número que poderia ser um teto de
                        # página: os pequenos são índices, os grandes são bytes.
                        if 50 <= int(encontrado.group(1)) <= 2000:
                            achados.append(
                                f"{caminho.relative_to(_RAIZ)}:{numero}: {linha.strip()}"
                            )
        assert not achados, "teto de página escrito à mão:\n" + "\n".join(achados)

    def test_a_migration_cobre_todas_as_marcas_do_banco(self) -> None:
        """Onze pontos: seis restrições, duas de tamanho e três funções.

        Enquanto o banco disser 100, subir o teto no código só muda onde a
        recusa acontece. Foi assim que a subida anterior ficou pela metade.

        Este teste carrega a migration e lê as estruturas dela, em vez de
        procurar nomes no texto: uma versão anterior passava mesmo com a função
        removida da lista, porque o nome continuava num comentário.
        """
        migration = _carrega_migration()
        restricoes = {nome for _tabela, nome, _coluna in migration._PAGE_LIMITS}
        assert restricoes == {
            "asset_pdf_raster_page_count_check",
            "asset_pdf_page_page_number_check",
            "asset_pdf_raster_candidate_page_count_check",
            "asset_pdf_page_candidate_page_number_check",
            "learning_pdf_annotation_page_number_check",
            "learning_pdf_bookmark_page_number_check",
        }
        funcoes = {assinatura.split("(")[0] for assinatura, _quais in migration._FUNCOES_COM_LIMITE}
        assert funcoes == {
            "app_private.authorize_entitled_pdf_page",
            "app_private.register_asset_ready_candidate_v2",
            # O gatilho: conhece o teto do pacote, e sem ele um documento de 200
            # MiB passa pelas restrições e morre no `INSERT`.
            "app_private.enforce_asset_pdf_page_limits",
        }
        assert migration._PACOTE_NOVO == 384 * _MIB


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


class TestRasterizacaoEmFaixas:
    """Dividir o documento entre processos, e só quando há onde correr.

    Medido no serviço isolado, com um documento real de 300 páginas:

        1 núcleo, 1 processo   → 149 s
        1 núcleo, 4 processos  → 207 s   ← paralelizar sem núcleo é mais lento
        4 núcleos, 1 processo  → 143 s
        4 núcleos, 4 processos →  38 s

    Cada processo abre o PDF inteiro para chegar à sua faixa. Sem núcleo, esse
    custo é pago quatro vezes e nada corre em paralelo.
    """

    def test_as_faixas_cobrem_o_documento_sem_sobrepor(self) -> None:
        for paginas in (1, 2, 83, 100, 299, 300):
            for processos in (1, 2, 3, 4):
                faixas = pdf_derivatives.page_ranges(paginas, processos)
                cobertas = [n for primeira, ultima in faixas for n in range(primeira, ultima + 1)]
                assert cobertas == list(range(1, paginas + 1)), (paginas, processos)

    def test_documento_menor_que_os_processos_nao_gera_faixa_vazia(self) -> None:
        # Uma faixa vazia viraria um `pdftoppm` sem página para desenhar, e o
        # `-f 3 -l 2` dele é um erro que derrubaria o documento inteiro.
        assert pdf_derivatives.page_ranges(2, 4) == [(1, 1), (2, 2)]

    def test_o_resto_e_distribuido_uma_pagina_por_faixa(self) -> None:
        # 83 em quatro: nenhuma faixa fica com duas páginas a mais que outra.
        faixas = pdf_derivatives.page_ranges(83, 4)
        tamanhos = [ultima - primeira + 1 for primeira, ultima in faixas]
        assert max(tamanhos) - min(tamanhos) <= 1

    def test_sem_nucleo_sobrando_roda_num_processo_so(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Com um núcleo, dividir custa 40% a mais — medido.

        É o caso do serviço isolado hoje, que tem 1 vCPU. Um número fixo de
        processos melhoraria o serviço num tamanho e pioraria no outro.
        """
        monkeypatch.setattr(pdf_derivatives, "_cpu_quota", lambda: 1)
        assert pdf_derivatives.raster_workers() == 1
        assert pdf_derivatives.page_ranges(300, pdf_derivatives.raster_workers()) == [(1, 300)]

    def test_com_nucleo_divide_ate_o_teto(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pdf_derivatives, "_cpu_quota", lambda: 4)
        assert pdf_derivatives.raster_workers() == 4
        monkeypatch.setattr(pdf_derivatives, "_cpu_quota", lambda: 16)
        assert pdf_derivatives.raster_workers() == pdf_derivatives._MAX_RASTER_WORKERS

    def test_a_cota_vem_do_cgroup_e_nao_da_maquina(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """`os.cpu_count()` conta os núcleos da máquina, não os concedidos.

        Dentro do contêiner ele diz 8 mesmo quando o cgroup concede 1 — e é
        assim que dividir o documento em quatro deixaria a rasterização 40% mais
        lenta, medido. Este teste lê um cgroup de mentira e confere que é ele
        quem manda.
        """
        cgroup = tmp_path / "cpu.max"
        cgroup.write_text("200000 100000")  # dois núcleos
        monkeypatch.setattr(pdf_derivatives, "os", _OsComMuitosNucleos())
        monkeypatch.setattr(pdf_derivatives, "Path", _PathQueApontaPara(cgroup))

        assert pdf_derivatives._cpu_quota() == 2, "a cota do cgroup foi ignorada"

    def test_sem_cgroup_legivel_assume_um_nucleo_por_seguranca(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Subestimar deixa o documento mais lento; superestimar o deixa mais
        # lento ainda, porque os processos disputam o mesmo núcleo.
        monkeypatch.setattr(pdf_derivatives, "Path", _PathQueApontaPara(tmp_path / "nao-existe"))
        monkeypatch.setattr(pdf_derivatives, "os", _OsSemContagem())

        assert pdf_derivatives._cpu_quota() == 1


class TestPrazoDaRasterizacao:
    def test_o_prazo_cobre_o_documento_cheio_no_pior_caso_medido(self) -> None:
        """O prazo antigo dava 164 páginas, abaixo do teto que o código dizia.

        Medido: 0,50 s por página no serviço isolado, com um núcleo e sem
        dividir — o pior caminho. Trezentas páginas são 149 s, e o prazo era de
        120: o documento morria antes de terminar, e a mensagem era "tempo
        esgotado", não "documento grande demais".
        """
        pior_caso_por_pagina = 0.50
        pior_caso = learning_pdf.MAX_PDF_PAGES * pior_caso_por_pagina
        assert pdf_derivatives._PDFTOPPM_TIMEOUT_SECONDS > pior_caso
        # E com folga: um serviço mais lento que o medido não pode virar um
        # documento recusado sem explicação.
        assert pdf_derivatives._PDFTOPPM_TIMEOUT_SECONDS >= pior_caso * 1.5


def _carrega_migration() -> Any:
    """A migration como módulo, para ler as estruturas dela e não o texto."""
    caminho = next(
        p for p in (_RAIZ / "apps/api/migrations/versions").iterdir() if p.name.startswith("0044_")
    )
    especificacao = importlib.util.spec_from_file_location("migracao_0044", caminho)
    assert especificacao is not None and especificacao.loader is not None
    modulo = importlib.util.module_from_spec(especificacao)
    especificacao.loader.exec_module(modulo)
    return modulo


class _OsComMuitosNucleos:
    """Uma máquina grande por baixo de uma cota pequena."""

    @staticmethod
    def cpu_count() -> int:
        return 64


class _OsSemContagem:
    @staticmethod
    def cpu_count() -> int | None:
        return None


class _PathQueApontaPara:
    """Faz qualquer caminho de cgroup cair no arquivo do teste."""

    def __init__(self, destino: Path) -> None:
        self._destino = destino

    def __call__(self, _caminho: str) -> Path:
        return self._destino
