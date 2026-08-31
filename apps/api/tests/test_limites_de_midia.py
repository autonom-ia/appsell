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
import time
from pathlib import Path
from typing import Any, Final

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

    def test_nenhum_arquivo_escreve_o_teto_de_pagina_a_mao(self) -> None:
        """Varre o código inteiro, e não a lista de arquivos de que eu lembrei.

        Esta é a terceira versão deste teste, e cada versão foi furada pelo
        arquivo que ela não olhava:

        - a primeira citava três arquivos e procurava `page_count`; escapou o
          BFF, com `safeInteger(page, 1, 100)`;
        - a segunda varria `lib/` e `app/`, mas só TypeScript; escapou o
          `reset_demo_tenant.py`, com o teto do pacote em bytes.

        Listar onde procurar é procurar o que já se sabe.
        """
        achados = _numeros_crus_de_pagina()
        assert not achados, "teto de página escrito à mão:\n" + "\n".join(achados)

    def test_nenhum_arquivo_escreve_o_teto_do_pacote_a_mao(self) -> None:
        # 128 MiB e 512 MiB foram os dois valores que conviviam com o do banco.
        antigos = ("128 * 1024 * 1024", "134217728", "512 * 1024 * 1024", "536870912")
        # Só onde o número pode ser o teto do pacote: 512 MiB também aparece
        # como cache de leitura e como teto de vídeo, que são outra coisa.
        contexto = re.compile(r"(raster|pdf_page|page_bytes|total_bytes|derivative)", re.IGNORECASE)
        achados: list[str] = []
        for caminho in _arquivos_de_codigo():
            for numero, linha in enumerate(caminho.read_text().splitlines(), 1):
                if linha.lstrip().startswith(("#", "*", "//")) or not contexto.search(linha):
                    continue
                if any(antigo in linha for antigo in antigos):
                    achados.append(f"{caminho.relative_to(_RAIZ)}:{numero}: {linha.strip()}")
        assert not achados, "teto de pacote escrito à mão:\n" + "\n".join(achados)

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
    def test_quem_espera_da_mais_prazo_do_que_quem_trabalha(self) -> None:
        """O prazo de fora tem de ser maior que o de dentro.

        O serviço isolado só manda o primeiro byte depois de verificar,
        normalizar, rasterizar, higienizar e montar o pacote. Com 180 s no
        cliente e 240 no rasterizador, os 240 eram decoração: quem decidia era
        o cliente, e o documento voltava como "serviço indisponível" em vez de
        "documento grande demais" — a mensagem errada, sobre a causa errada.
        """
        assert pdf_derivatives._SANDBOX_TIMEOUT_SECONDS > pdf_derivatives._PDFTOPPM_TIMEOUT_SECONDS
        # E com margem para o resto do caminho, que não é pouco: download,
        # antivírus, normalização e a montagem do pacote.
        sobra = pdf_derivatives._SANDBOX_TIMEOUT_SECONDS - pdf_derivatives._PDFTOPPM_TIMEOUT_SECONDS
        assert sobra >= 120

    def test_o_prazo_do_cliente_e_o_que_o_cliente_usa(self) -> None:
        # Um número declarado e outro escrito na chamada é como os 240 s viraram
        # decoração da primeira vez.
        fonte = Path(pdf_derivatives.__file__).read_text()
        assert "httpx.Timeout(_SANDBOX_TIMEOUT_SECONDS" in fonte
        assert not re.search(r"httpx\.Timeout\(\d", fonte)

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


def _arquivos_de_codigo() -> list[Path]:
    """Todo o código vivo do repositório, em qualquer linguagem."""
    arquivos: list[Path] = []
    for pasta, padroes in (
        ("lib", ("*.ts", "*.tsx")),
        ("app", ("*.ts", "*.tsx")),
        ("apps/api/src", ("*.py",)),
        # Fora do código da aplicação, mas com os mesmos números dentro: foi por
        # aqui que o `tmpfs` de 384 MiB sobreviveu à conta que dizia 1,5 GiB.
        ("deploy", ("*.sh", "*.yaml", "*.yml")),
        ("scripts", ("*.sh", "*.mjs")),
    ):
        for padrao in padroes:
            arquivos.extend(
                caminho
                for caminho in sorted((_RAIZ / pasta).rglob(padrao))
                if "node_modules" not in caminho.parts and not caminho.name.endswith(".d.ts")
            )
    return arquivos


def _numeros_crus_de_pagina() -> list[str]:
    suspeito = re.compile(r"\bpage(?:_count|Count|_number|Number|s)?\b[^\n]{0,40}?\b(\d{2,4})\b")
    achados: list[str] = []
    for caminho in _arquivos_de_codigo():
        for numero, linha in enumerate(caminho.read_text().splitlines(), 1):
            if "MAX_PDF_PAGES" in linha or linha.lstrip().startswith(("#", "*", "//")):
                continue
            # Bytes e graus não são contagem de página.
            if re.search(r"(1024|byte|360|_size)", linha, re.IGNORECASE):
                continue
            for encontrado in suspeito.finditer(linha):
                # Só interessa o que poderia ser um teto: os pequenos são
                # índices, os grandes são bytes.
                if 50 <= int(encontrado.group(1)) <= 2000:
                    achados.append(f"{caminho.relative_to(_RAIZ)}:{numero}: {linha.strip()}")
    return achados


class TestOrcamentoDoLease:
    """O trabalho todo cabe nos 600 s que o banco concede ao worker.

    O lease não se renova: passado o prazo, outro worker pode pegar o mesmo
    trabalho. Um documento que não termina a tempo não falha com uma mensagem —
    ele é refeito, e refeito de novo.

    Medido de ponta a ponta, com um documento real de 300 páginas montado a
    partir do playbook do vendedor, numa cópia do serviço de produção:

        download do PDF (12 MiB)                0,2 s
        antivírus                               3,0 s
        raster + higienização + pacote (1 CPU) 208,0 s
        upload das 300 páginas, uma a uma        9,5 s
        banco: registro e finalização            5,0 s
        ------------------------------------------------
        total                                  225,7 s   de 600 s

    Com quatro núcleos no serviço isolado, 107 s. O caminho lento é o que vale
    como garantia, e ele sobra 374 s.
    """

    #: O que cada fase custou, em segundos, com um núcleo — o pior caminho.
    CUSTO_MEDIDO: Final = {
        "download": 0.2,
        "antivirus": 3.0,
        "raster_e_pacote": 208.0,
        "upload_das_paginas": 9.5,
        "banco": 5.0,
    }
    LEASE_SEGUNDOS: Final = 600

    def test_o_trabalho_cabe_no_lease_com_folga(self) -> None:
        total = sum(self.CUSTO_MEDIDO.values())
        assert total < self.LEASE_SEGUNDOS
        # Duas vezes, não por gosto: a medição é de uma máquina ociosa, e o
        # documento chega quando ela não está.
        assert self.LEASE_SEGUNDOS / total >= 2.0

    def test_o_prazo_do_cliente_cobre_a_fase_mais_cara(self) -> None:
        # O serviço isolado só responde depois de rasterizar, higienizar e
        # montar o pacote: 208 s no pior caminho. Quem espera precisa esperar
        # mais que isso.
        assert pdf_derivatives._SANDBOX_TIMEOUT_SECONDS > self.CUSTO_MEDIDO["raster_e_pacote"]

    def test_o_lease_do_worker_e_o_que_este_orcamento_assume(self) -> None:
        """Se o lease encolher, esta conta deixa de valer sem avisar.

        Lendo a constante, e não procurando o número no arquivo: `600` também
        aparece nos comentários que explicam a faixa que o banco aceita, então a
        busca textual passaria com a constante já trocada.
        """
        from appsell_api.asset_worker_runner import _ASSET_LEASE_SECONDS

        assert _ASSET_LEASE_SECONDS == self.LEASE_SEGUNDOS


class TestRasterizarQuatroProcessos:
    """O laço de faixas, executado — não só a matemática das faixas.

    Um `pdftoppm` de mentira, que dorme e fala pelo `stderr`, é o suficiente
    para provar as duas coisas que a revisão apontou e que nenhum teste
    exercitava: o prazo é do conjunto, e um processo que fala muito não trava os
    outros.
    """

    def _falso_pdftoppm(self, diretorio: Path, *, dorme: float, ruido: int = 0) -> str:
        """Um rasterizador que demora e escreve no `stderr`, sem desenhar nada."""
        atalho = diretorio / "pdftoppm-falso"
        atalho.write_text(
            "#!/bin/sh\n"
            f"[ {ruido} -gt 0 ] && head -c {ruido} /dev/zero | tr '\\0' 'x' >&2\n"
            f"sleep {dorme}\n"
            "exit 0\n"
        )
        atalho.chmod(0o700)
        return str(atalho)

    def _falso_escalonado(self, diretorio: Path) -> str:
        """Cada faixa demora mais que a anterior, pela página em que começa.

        É o que distingue um prazo do conjunto de um prazo por faixa: com
        faixas iguais, esperar uma de cada vez dá no mesmo, porque a segunda já
        terminou quando chega a vez dela. Com tempos crescentes, o prazo por
        faixa vai sendo renovado e a última escapa.
        """
        atalho = diretorio / "pdftoppm-escalonado"
        atalho.write_text(
            "#!/bin/sh\n"
            "primeira=1\n"
            "while [ $# -gt 0 ]; do\n"
            '  [ "$1" = "-f" ] && primeira=$2\n'
            "  shift\n"
            "done\n"
            # 1ª faixa dorme 1 s, 2ª dorme 2 s, e assim por diante.
            "faixa=$(( (primeira - 1) / 75 + 1 ))\n"
            "sleep $faixa\n"
            "exit 0\n"
        )
        atalho.chmod(0o700)
        return str(atalho)

    def test_o_prazo_conta_do_lancamento_e_nao_de_cada_espera(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Quatro faixas com 1, 2, 3 e 4 s, e um prazo de 3 s: tem de estourar.

        Esperando uma de cada vez com prazo próprio, a conta era outra: a
        primeira volta em 1 s, a segunda em mais 1 s, a terceira em mais 1 s, e
        a quarta ganha 3 s inteiros a partir daí — termina em 4 s e passa. O
        prazo virava a soma, e o cliente desistia antes com uma mensagem que não
        dizia nada disso.
        """
        monkeypatch.setattr(pdf_derivatives, "raster_workers", lambda: 4)
        monkeypatch.setattr(pdf_derivatives, "_PDFTOPPM_TIMEOUT_SECONDS", 3)
        falso = self._falso_escalonado(tmp_path)

        with pytest.raises(pdf_derivatives.PdfDerivativeRejected, match="timed out"):
            pdf_derivatives._rasterize_in_ranges(
                falso, source=tmp_path / "x.pdf", output_prefix=tmp_path / "page", page_count=300
            )

    def test_o_prazo_e_do_conjunto_e_nao_de_cada_faixa(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Quatro faixas de 2 s não podem levar quatro prazos de 3 s.

        Esperar uma de cada vez, com um prazo próprio para cada, somava os
        prazos: quatro faixas podiam gastar quatro vezes o limite, e o cliente
        desistia antes com uma mensagem que não dizia nada disso.

        Aqui o limite é 3 s e cada faixa dorme 2 s. Correndo juntas, terminam em
        ~2 s. Se o prazo voltasse a ser de cada uma, isto passaria mesmo com as
        faixas em série — por isso o teste também mede o relógio.
        """
        monkeypatch.setattr(pdf_derivatives, "raster_workers", lambda: 4)
        monkeypatch.setattr(pdf_derivatives, "_PDFTOPPM_TIMEOUT_SECONDS", 3)
        falso = self._falso_pdftoppm(tmp_path, dorme=2)

        comecou = time.monotonic()
        pdf_derivatives._rasterize_in_ranges(
            falso, source=tmp_path / "x.pdf", output_prefix=tmp_path / "page", page_count=300
        )
        gasto = time.monotonic() - comecou

        # Em paralelo: perto de 2 s. Em série seriam 8 s, e o prazo de 3 s teria
        # de ter estourado — de um jeito ou de outro, isto não passa.
        assert gasto < 4, f"as faixas não correram juntas: {gasto:.1f} s"

    def test_uma_faixa_lenta_estoura_o_prazo_do_conjunto(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(pdf_derivatives, "raster_workers", lambda: 4)
        monkeypatch.setattr(pdf_derivatives, "_PDFTOPPM_TIMEOUT_SECONDS", 1)
        falso = self._falso_pdftoppm(tmp_path, dorme=5)

        with pytest.raises(pdf_derivatives.PdfDerivativeRejected, match="timed out"):
            pdf_derivatives._rasterize_in_ranges(
                falso, source=tmp_path / "x.pdf", output_prefix=tmp_path / "page", page_count=300
            )

    def test_faixa_que_fala_muito_nao_trava_as_outras(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Um `stderr` que ninguém lê enche e trava quem está do outro lado.

        Com `stderr=PIPE` e a espera feita uma faixa de cada vez, as faixas
        ainda não esperadas ficavam paradas com o cano cheio — sem ninguém
        saber, e sem contar o tempo delas. O erro vai para arquivo.
        """
        monkeypatch.setattr(pdf_derivatives, "raster_workers", lambda: 4)
        monkeypatch.setattr(pdf_derivatives, "_PDFTOPPM_TIMEOUT_SECONDS", 10)
        # 256 KiB por faixa: muito acima do cano do sistema, que trava em 64 KiB.
        falso = self._falso_pdftoppm(tmp_path, dorme=0.2, ruido=256 * 1024)

        comecou = time.monotonic()
        with pytest.raises(pdf_derivatives.PdfDerivativeRejected, match="failed"):
            # Fala demais é documento recusado — mas depressa, e não travado.
            pdf_derivatives._rasterize_in_ranges(
                falso, source=tmp_path / "x.pdf", output_prefix=tmp_path / "page", page_count=300
            )
        assert time.monotonic() - comecou < 5, "uma faixa falante travou as outras"

    def test_nao_sobra_processo_nem_arquivo_de_erro(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Um documento recusado não pode deixar o serviço isolado ocupado, nem
        # lixo no diretório que a coleta das páginas depois vai varrer.
        monkeypatch.setattr(pdf_derivatives, "raster_workers", lambda: 4)
        monkeypatch.setattr(pdf_derivatives, "_PDFTOPPM_TIMEOUT_SECONDS", 1)
        falso = self._falso_pdftoppm(tmp_path, dorme=5)

        with pytest.raises(pdf_derivatives.PdfDerivativeRejected):
            pdf_derivatives._rasterize_in_ranges(
                falso, source=tmp_path / "x.pdf", output_prefix=tmp_path / "page", page_count=300
            )

        assert not list(tmp_path.glob(".erro-*")), "ficou arquivo de erro para trás"


class TestEnvelopeDoWorker:
    def test_o_tmpfs_cobre_o_pior_caso_do_pipeline(self) -> None:
        """O `/tmp` do worker precisa caber o que o pipeline segura junto.

        Com 300 páginas o worker mantém, ao mesmo tempo, o PDF de origem, o
        pacote recebido, a cópia do pacote feita na verificação, as páginas
        extraídas e o PDF extraído — 1.556 MiB no pior caso. Eram 384 MiB, e o
        documento grande falhava com disco cheio.
        """
        script = (_RAIZ / "deploy/easypanel-native/ensure-asset-worker.sh").read_text()
        tamanho = re.search(r"tmpfs-size=(\d+)", script)
        assert tamanho is not None, "o worker perdeu o `/tmp` dedicado"

        pior_caso = (
            pdf_derivatives.MAX_PDF_PAGE_ARCHIVE_BYTES  # pacote recebido
            + pdf_derivatives.MAX_PDF_PAGE_ARCHIVE_BYTES  # cópia na verificação
            + pdf_derivatives.MAX_PDF_DERIVATIVE_BYTES  # páginas extraídas
        )
        assert int(tamanho.group(1)) >= pior_caso, (
            f"`/tmp` de {int(tamanho.group(1)) / _MIB:.0f} MiB não cobre "
            f"{pior_caso / _MIB:.0f} MiB de pior caso"
        )

    def test_a_memoria_cobre_o_tmpfs_mais_o_processo(self) -> None:
        # O `tmpfs` conta contra o cgroup: o limite de memória precisa cobrir os
        # arquivos e os processos ao mesmo tempo.
        script = (_RAIZ / "deploy/easypanel-native/ensure-asset-worker.sh").read_text()
        tmpfs = int(re.search(r"tmpfs-size=(\d+)", script).group(1))  # type: ignore[union-attr]
        memoria = re.search(r"--limit-memory (\d+)G", script)
        assert memoria is not None, "o worker perdeu o limite de memória"
        assert int(memoria.group(1)) * 1024 * _MIB > tmpfs


#: O  declarado no compose — do serviço, não deste teste.
_TMPFS = re.compile(r"/tmp:size=(\d+)")  # noqa: S108


class TestOEnvelopeDoSandbox:
    """Quem rasteriza precisa caber no que declarou que vai usar.

    O `/tmp` do serviço é `tmpfs`, e `tmpfs` conta contra o cgroup: declarar 3
    GiB de disco dentro de 1 GiB de memória é prometer espaço que não existe.
    Um documento dentro dos limites aceitos morria por falta de memória antes de
    ser recusado com uma mensagem.
    """

    ARQUIVOS: Final = (
        "compose.yaml",
        "deploy/easypanel/compose.runtime-preview.yaml",
    )

    def _bloco_do_sandbox(self, caminho: str) -> str:
        conteudo = (_RAIZ / caminho).read_text()
        inicio = conteudo.index("\n  pdf-sandbox:")
        seguinte = conteudo.find("\n  ", conteudo.index("mem_limit", inicio))
        fim = conteudo.find("\n  sell-web:", inicio)
        return conteudo[inicio : fim if fim > 0 else seguinte + 4000]

    def test_a_memoria_cobre_o_tmpfs_e_o_processo(self) -> None:
        for caminho in self.ARQUIVOS:
            bloco = self._bloco_do_sandbox(caminho)
            memoria = re.search(r"mem_limit:\s*(\d+)g", bloco)
            tmpfs = re.search(_TMPFS, bloco)
            assert memoria and tmpfs, caminho
            cabe = int(memoria.group(1)) * 1024 * _MIB
            assert int(tmpfs.group(1)) < cabe, (
                f"{caminho}: `/tmp` de {int(tmpfs.group(1)) / _MIB:.0f} MiB não cabe em "
                f"{memoria.group(1)} GiB de memória"
            )

    def test_o_tmpfs_cobre_o_pior_caso_que_o_codigo_declara(self) -> None:
        # `MAX_PDF_JOB_TEMP_BYTES` é a conta do próprio módulo: pacote, cópia,
        # páginas e as cópias do PDF que o trabalho segura ao mesmo tempo.
        for caminho in self.ARQUIVOS:
            tmpfs = re.search(_TMPFS, self._bloco_do_sandbox(caminho))
            assert tmpfs is not None, caminho
            assert int(tmpfs.group(1)) >= pdf_derivatives.MAX_PDF_JOB_TEMP_BYTES, caminho

    def test_tem_nucleo_para_a_divisao_em_faixas_valer(self) -> None:
        """Medido: 208 s com um núcleo, 89 s com quatro.

        Com um só, o código roda sequencial de propósito — dividir sem ter onde
        correr é mais lento. O número de núcleos aqui é o que decide qual dos
        dois caminhos o serviço toma.
        """
        for caminho in self.ARQUIVOS:
            nucleos = re.search(r"cpus:\s*([\d.]+)", self._bloco_do_sandbox(caminho))
            assert nucleos is not None, caminho
            assert float(nucleos.group(1)) >= pdf_derivatives._MAX_RASTER_WORKERS, caminho


class TestAMigrationNaoQuebraOReadiness:
    """Uma migration que sobe a versão precisa ensinar o banco a reconhecê-la.

    `schema_ready()` guarda a lista das migrations conhecidas, e o `/health/ready`
    da API consulta ela. Subir a versão sem estender a lista deixa o banco "não
    pronto" para sempre — e, desde que o sandbox passou a contar no `ready`,
    isso não é um número errado numa resposta: o healthcheck do contêiner
    derruba a task, e o serviço entra em laço de reinício.

    Seria o deploy desta PR derrubando a API inteira.
    """

    def test_a_migration_estende_a_lista_de_versoes_conhecidas(self) -> None:
        migration = _carrega_migration()
        assert migration._READY_ATE_0044.endswith("'0044_pdf_de_trezentas_paginas'))")
        # A versão nova entra ao lado da anterior, e não no lugar dela: a lista
        # é de tudo que o banco reconhece, não só do último degrau.
        assert "'0043_material_role_by_media'" in migration._READY_ATE_0044
        # As duas cópias da função carregam a lista, e as duas precisam mudar.
        assert len(migration._BASES_DO_READY) == 2

    def test_o_reset_do_demo_espera_a_versao_nova(self) -> None:
        from appsell_api.reset_demo_tenant import EXPECTED_MIGRATION

        migration = _carrega_migration()
        assert EXPECTED_MIGRATION == migration.revision

    def test_nenhum_teste_ficou_preso_na_versao_anterior(self) -> None:
        # Seis testes fixavam `0043` e passariam a falhar depois do upgrade.
        anterior = "0043_material_role_by_media"
        presos = [
            f"{caminho.relative_to(_RAIZ)}:{numero}"
            for caminho in sorted((_RAIZ / "apps/api/tests").glob("*.py"))
            # Este arquivo cita a versão anterior para poder procurá-la.
            if caminho.name != Path(__file__).name
            for numero, linha in enumerate(caminho.read_text().splitlines(), 1)
            if anterior in linha
        ]
        assert not presos, "testes presos na versão anterior:\n" + "\n".join(presos)
