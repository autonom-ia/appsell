"""Parser de conteúdo de terceiro roda isolado, e não no processo da API.

O arquivo que o `ffmpeg` abre é escolhido pelo vendedor. O processo da API tem,
no ambiente, a senha do banco, as chaves de assinatura de contexto e as
credenciais do storage — um bug de parsing ali não custa um vídeo, custa o
tenant inteiro.

O projeto já tinha o padrão certo: `video_verifier` manda mídia não confiável
para o `pdf-sandbox` por socket, num container sem rede, sem essas credenciais,
e com o processo reciclado depois de cada trabalho. Mas o padrão estava só no
costume — em 30/08/2026 escrevi `pdf_link_video` chamando `ffmpeg` direto de
dentro da API, e nada no CI reclamou. Quem reclamou foi o review, depois de a
funcionalidade estar pronta.

Este teste transforma o costume em regra: se um módulo alcançável a partir de
uma rota da API executar binário de mídia, o CI reprova antes do review.
"""

from __future__ import annotations

import ast
from pathlib import Path

FONTE = Path(__file__).resolve().parents[1] / "src" / "appsell_api"

#: Os binários que abrem conteúdo de terceiro.
BINARIOS = ("ffmpeg", "ffprobe", "qpdf", "gs", "pdftoppm", "pdfium", "mutool")

#: Os módulos que executam um deles hoje, e a razão de cada um.
#:
#: Esta lista é o ponto da varredura. Ela não impede que estes existam — impede
#: que **apareça um novo** sem ninguém decidir. Foi exatamente o que faltou:
#: escrevi `pdf_link_video` chamando `ffmpeg` de dentro da API e nada reclamou.
#:
#: Acrescentar um nome aqui é uma decisão consciente, e a pergunta que ela obriga
#: a responder é: quem escolhe o arquivo que este binário vai abrir? Se for o
#: vendedor, o lugar é o `pdf-sandbox`.
EXECUTAM_BINARIO = {
    # Convertem o vídeo do vendedor e o PDF dele. Chamados de dentro do
    # `pdf-sandbox`, que não tem rede nem as credenciais da API.
    "video_remux.py",
    "video_verifier.py",
    "pdf_raster.py",
    "pdf_derivatives.py",
    # Rasteriza a pagina para ler o QR de um botao sem destino. Roda num
    # processo proprio, lancado de dentro do sandbox, para que o prazo possa
    # mata-lo — e a varredura acusou os dois arquivos assim que apareceram, que
    # e o que ela existe para fazer.
    "pdf_link_qr_decode.py",
}

#: Por onde uma requisição de usuário entra na API.
ROTAS = (
    "learning_routes.py",
    "asset_routes.py",
    "catalog_routes.py",
    "checkout_routes.py",
    "identity_routes.py",
)


def _modulos_locais(caminho: Path) -> set[str]:
    """Os módulos do pacote que este arquivo importa."""
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    nomes: set[str] = set()
    for node in ast.walk(arvore):
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            nomes.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("appsell_api."):
                    nomes.add(alias.name.split(".")[1])
    return nomes


def _alcancaveis(partida: str) -> set[str]:
    """Tudo que a rota importa, direta ou indiretamente."""
    vistos: set[str] = set()
    fila = [partida]
    while fila:
        atual = fila.pop()
        if atual in vistos:
            continue
        vistos.add(atual)
        arquivo = FONTE / f"{atual}.py"
        if not arquivo.exists():
            continue
        fila.extend(_modulos_locais(arquivo))
    return vistos


def _executa_binario_de_midia(caminho: Path) -> bool:
    texto = caminho.read_text(encoding="utf-8")
    if "subprocess" not in texto and "Popen" not in texto:
        return False
    return any(f'"{binario}"' in texto or f"'{binario}'" in texto for binario in BINARIOS)


def test_nenhum_modulo_novo_executa_binario_de_midia() -> None:
    """Quem executa binário de mídia é uma lista, e ela não cresce sozinha.

    A alternativa — perguntar quais módulos a rota alcança — acusa qualquer um
    que só *contenha* a função, e a lista de exceções fica tão grande que a
    regra deixa de valer. Aqui a pergunta é outra e tem resposta exata: apareceu
    um executor novo?
    """
    encontrados = {
        arquivo.name for arquivo in sorted(FONTE.glob("*.py")) if _executa_binario_de_midia(arquivo)
    }
    novos = sorted(encontrados - EXECUTAM_BINARIO)
    assert not novos, "\n".join(
        [
            "Módulo novo executando binário de mídia:",
            *novos,
            "",
            "Quem escolhe o arquivo que este binário vai abrir? Se for o vendedor,",
            "o lugar é o `pdf-sandbox`: container sem rede, sem as credenciais da",
            "API e com o processo reciclado a cada trabalho. Mande pelo socket,",
            "como `pdf_link_video` faz em `remux_in_sandbox`.",
            "",
            "Se o arquivo for produzido pela nossa própria infraestrutura,",
            "acrescente o nome a EXECUTAM_BINARIO com a razão escrita.",
        ]
    )

    # A lista também não encolhe em silêncio: um nome que sai daqui sem sair do
    # código é a varredura perdendo alcance sem ninguém perceber.
    assert not sorted(EXECUTAM_BINARIO - encontrados)


def test_o_modulo_que_converte_video_nao_e_alcancavel_pela_api() -> None:
    # `video_remux` executa `ffmpeg` sobre o arquivo do vendedor. Ele existe
    # para o serviço isolado, e é o caso concreto que motivou esta varredura.
    assert "video_remux" not in _alcancaveis("learning_routes")
    assert "video_remux" in _alcancaveis("pdf_sandbox_service")


def test_a_rota_do_aluno_nao_alcanca_quem_executa_ffprobe() -> None:
    """A rota que serve o aluno não precisa enxergar o verificador de mídia.

    `video_verifier` executa `ffprobe`. Quem o alcança legitimamente é
    `asset_routes`, por causa do worker que verifica o que o vendedor enviou. A
    rota do aluno só entrega o que já foi verificado.

    Esta linha existe porque em 31/08/2026 eu quase a cruzei: importei duas
    constantes de `video_verifier` dentro de `pdf_link_video`, que a rota do
    aluno alcança, e com isso alarguei o que a API enxerga em troca de dois
    números. Nada teria reclamado.
    """
    assert "video_verifier" not in _alcancaveis("learning_routes")
    assert "video_verifier" in _alcancaveis("asset_routes")


def test_a_varredura_enxerga_o_que_deveria_enxergar() -> None:
    # Sem isto, um erro no caminho ou no analisador faria o teste passar por não
    # ler módulo nenhum — o pior desfecho possível para uma varredura.
    alcancaveis = _alcancaveis("learning_routes")
    assert len(alcancaveis) > 10
    assert "pdf_link_video" in alcancaveis
    assert _executa_binario_de_midia(FONTE / "video_remux.py") is True
    assert _executa_binario_de_midia(FONTE / "pdf_link_video.py") is False
