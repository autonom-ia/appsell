"""O que acontece com os links do PDF quando o serviço isolado está ocupado.

Ele tem **um lugar só**, e nele cabem a leitura dos links, a rasterização das
páginas e a conversão dos vídeos. Quem chega enquanto ele trabalha leva `429`.

Isso virava um documento sem link nenhum: o aluno abria o guia e não havia
botão, sem erro na tela e sem registro. Dois alunos abrindo PDFs ao mesmo tempo
já bastavam. Ficou mais fácil de acontecer quando o preparo dos vídeos passou a
começar sozinho, na abertura da aula.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from appsell_api import learning_routes
from appsell_api.pdf_links import PdfLinksBusy, PdfLinksRejected, PdfLinksUnavailable


def _resposta(status: int, corpo: bytes = b"{}") -> httpx.Response:
    return httpx.Response(
        status_code=status,
        headers={"content-type": "application/json"},
        content=corpo,
        request=httpx.Request("POST", "http://pdf-sandbox/links"),
    )


class TestOcupadoNaoEDocumentoSemLinks:
    def test_o_429_vira_ocupado_e_nao_indisponivel(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """A distinção existe no lugar onde a resposta chega.

        `PdfLinksBusy` herda de `PdfLinksUnavailable` de propósito: quem não
        souber da diferença continua tratando como antes, e quem souber insiste.
        """
        from appsell_api import pdf_links

        monkeypatch.setenv("APPSELL_API_PDF_SANDBOX_URL", "http://pdf-sandbox")

        class _Cliente:
            def __init__(self, **_kwargs: Any) -> None: ...
            def __enter__(self) -> _Cliente:
                return self

            def __exit__(self, *_args: Any) -> None: ...
            def post(self, *_args: Any, **_kwargs: Any) -> httpx.Response:
                return _resposta(429)

        monkeypatch.setattr(httpx, "Client", _Cliente)
        arquivo = tmp_path / "documento.pdf"
        arquivo.write_bytes(b"%PDF-1.4\n")
        with arquivo.open("rb") as fonte, pytest.raises(PdfLinksBusy):
            pdf_links.read_remote_pdf_link_manifest(
                fonte,
                expected_size=9,
                expected_sha256_hex="a" * 64,
                expected_page_count=1,
            )
        assert issubclass(PdfLinksBusy, PdfLinksUnavailable)

    async def test_insiste_enquanto_esta_ocupado_e_entrega_os_links(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """O caso que importa: ocupado agora, livre daqui a pouco."""
        monkeypatch.setattr(learning_routes, "PDF_LINKS_RETRY_SECONDS", 0.01)
        tentativas = 0

        def _ler() -> dict[str, Any]:
            nonlocal tentativas
            tentativas += 1
            if tentativas < 3:
                raise PdfLinksBusy("ocupado")
            return {"links": [{"id": "abcdef0123456789", "kind": "video"}]}

        lido = await learning_routes._read_links_waiting_for_the_sandbox(_ler)

        assert tentativas == 3
        assert lido["links"][0]["id"] == "abcdef0123456789"

    async def test_desiste_depois_do_teto_em_vez_de_ficar_pendurado(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Insistir tem fim.

        Sem teto, uma conversão travada deixaria a requisição do aluno pendurada
        até o prazo dela. Passado o teto, o documento volta sem links — como
        voltava antes —, mas só depois de tentar.
        """
        monkeypatch.setattr(learning_routes, "PDF_LINKS_RETRY_SECONDS", 0.01)
        tentativas = 0

        def _ocupado() -> dict[str, Any]:
            nonlocal tentativas
            tentativas += 1
            raise PdfLinksBusy("ocupado")

        with pytest.raises(PdfLinksBusy):
            await learning_routes._read_links_waiting_for_the_sandbox(_ocupado)
        assert tentativas == learning_routes.PDF_LINKS_ATTEMPTS

    async def test_nao_insiste_quando_o_documento_foi_recusado(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Recusa não é ocupação.

        Um PDF grande demais ou corrompido dá o mesmo resultado em todas as
        tentativas. Insistir só faria o aluno esperar mais pela mesma resposta.
        """
        monkeypatch.setattr(learning_routes, "PDF_LINKS_RETRY_SECONDS", 0.01)
        tentativas = 0

        def _recusado() -> dict[str, Any]:
            nonlocal tentativas
            tentativas += 1
            raise PdfLinksRejected("documento recusado")

        with pytest.raises(PdfLinksRejected):
            await learning_routes._read_links_waiting_for_the_sandbox(_recusado)
        assert tentativas == 1

    async def test_nao_insiste_quando_o_servico_esta_fora_do_ar(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fora do ar não é ocupado.

        `PdfLinksBusy` herda de `PdfLinksUnavailable`, e por isso o `except`
        precisa nomear a filha. Pegar a mãe faria toda indisponibilidade de
        verdade — serviço caído, soquete ausente — custar quatro tentativas e a
        espera inteira antes de dar a mesma resposta.
        """
        monkeypatch.setattr(learning_routes, "PDF_LINKS_RETRY_SECONDS", 0.01)
        tentativas = 0

        def _fora_do_ar() -> dict[str, Any]:
            nonlocal tentativas
            tentativas += 1
            raise PdfLinksUnavailable("serviço fora do ar")

        with pytest.raises(PdfLinksUnavailable):
            await learning_routes._read_links_waiting_for_the_sandbox(_fora_do_ar)
        assert tentativas == 1

    async def test_a_espera_total_cabe_no_tempo_de_abrir_uma_aula(self) -> None:
        # Quem espera é a requisição do aluno. O teto existe para ela não virar
        # uma conexão pendurada atrás de uma conversão de vídeo.
        total = learning_routes.PDF_LINKS_ATTEMPTS * learning_routes.PDF_LINKS_RETRY_SECONDS
        assert total <= 10
        assert learning_routes.PDF_LINKS_ATTEMPTS >= 2


def test_a_rota_dos_links_usa_a_leitura_que_insiste() -> None:
    """`_protected_pdf_links` chama a versão que insiste, e não `to_thread` cru.

    Os outros testes exercitam o auxiliar direto e passariam com a rota voltando
    a chamar a leitura sem insistência — e o sintoma em produção seria o mesmo
    de antes: PDF sem botão, calado.
    """
    from pathlib import Path

    fonte = Path(learning_routes.__file__).read_text()
    corpo = fonte[fonte.index("async def _protected_pdf_links(") :]
    corpo = corpo[: corpo.index("\nasync def _read_links_waiting_for_the_sandbox(")]
    assert "await _read_links_waiting_for_the_sandbox(read_links)" in corpo
    assert "await to_thread.run_sync(read_links)" not in corpo
