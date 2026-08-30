"""O vídeo do PDF vira MP4, e só busca o que a allowlist permite."""

from __future__ import annotations

import time
from pathlib import Path

import anyio
import anyio.lowlevel
import pytest

from appsell_api import pdf_link_video
from appsell_api.pdf_link_video import (
    PdfLinkVideoRejected,
    PreparationRegistry,
    derivative_key,
    download_source,
    origin_validator,
    trusted_video_target,
)
from appsell_api.pdf_links import VIDEO_EXTENSIONS

ALLOWED = "https://midia.example.test"


SELLER = "aea20a25-9889-47a5-bdbf-8588aae3ba1c"
OUTRO_SELLER = "1c2d3e4f-5678-4abc-8def-9012345678ab"


class TestTrustedTarget:
    """Quem busca agora é o servidor, então o alvo é conferido antes."""

    def test_accepts_an_allowed_origin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        url = f"{ALLOWED}/aula.mov"
        assert trusted_video_target(url) == url

    def test_refuses_an_origin_outside_the_allowlist(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        assert trusted_video_target("https://outro.example.test/aula.mov") is None

    def test_refuses_everything_when_no_allowlist_is_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Sem allowlist o servidor não busca nada. O padrão inseguro seria
        # "vazio significa tudo liberado", e aqui o endereço vem de um PDF de
        # terceiro: é o servidor fazendo requisição a mando de dado externo.
        monkeypatch.delenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", raising=False)
        assert trusted_video_target(f"{ALLOWED}/aula.mov") is None

    @pytest.mark.parametrize(
        "url",
        [
            "http://midia.example.test/aula.mov",
            "https://user:senha@midia.example.test/aula.mov",
            "https://midia.example.test:8443/aula.mov",
            "https://169.254.169.254/latest/meta-data",
            "file:///etc/passwd",
            "",
        ],
    )
    def test_refuses_hostile_and_malformed_targets(
        self, monkeypatch: pytest.MonkeyPatch, url: str
    ) -> None:
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        assert trusted_video_target(url) is None

    def test_refuses_a_value_that_is_not_a_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        assert trusted_video_target(None) is None
        assert trusted_video_target(b"https://midia.example.test/a.mov") is None


class TestDerivativeKey:
    def test_the_same_address_always_lands_on_the_same_object(self) -> None:
        assert derivative_key(SELLER, "https://a.test/x.mov") == derivative_key(
            SELLER, "https://a.test/x.mov"
        )

    def test_different_addresses_do_not_collide(self) -> None:
        assert derivative_key(SELLER, "https://a.test/x.mov") != derivative_key(
            SELLER, "https://a.test/y.mov"
        )

    def test_two_sellers_citing_the_same_address_do_not_share_the_object(self) -> None:
        # Uma chave só do endereço faria o segundo vendedor receber a cópia que
        # o primeiro preparou — de um conteúdo que aquele endereço talvez nem
        # sirva mais.
        assert derivative_key(SELLER, "https://a.test/x.mov") != derivative_key(
            OUTRO_SELLER, "https://a.test/x.mov"
        )

    def test_the_key_carries_no_characters_from_the_address(self) -> None:
        # A chave é um resumo, então nem o host nem o caminho da origem ficam
        # legíveis no nome do objeto guardado.
        key = derivative_key(SELLER, "https://midia.example.test/pasta secreta/aula.mov")
        assert "midia" not in key and "secreta" not in key
        assert key.startswith("derivatives/pdf-link-video/") and key.endswith(".mp4")


class TestPreparationRegistry:
    """O aluno insistindo não pode virar um `ffmpeg` por clique."""

    def test_only_the_first_caller_starts_the_work(self) -> None:
        registry = PreparationRegistry()
        assert registry.start("chave") is True
        assert registry.start("chave") is False
        assert registry.running("chave") is True

    def test_finishing_frees_the_key_for_a_later_attempt(self) -> None:
        registry = PreparationRegistry()
        registry.start("chave")
        registry.finish("chave")
        assert registry.running("chave") is False
        assert registry.start("chave") is True

    def test_different_keys_do_not_block_each_other(self) -> None:
        registry = PreparationRegistry()
        assert registry.start("uma") is True
        assert registry.start("outra") is True

    def test_a_rejected_key_is_not_attempted_again(self) -> None:
        # A tela sonda dezenas de vezes. Sem lembrar da recusa, cada sondagem
        # recomeça um download e uma conversão que já se sabe que não vão dar.
        registry = PreparationRegistry()
        registry.start("chave")
        registry.reject("chave")
        registry.finish("chave")
        assert registry.rejected("chave") is True
        assert registry.start("chave") is False


class TestRecognisedContainers:
    """O leitor abre no player o que o servidor sabe preparar."""

    def test_the_classic_pair_is_still_recognised(self) -> None:
        assert ".mov" in VIDEO_EXTENSIONS
        assert ".mp4" in VIDEO_EXTENSIONS

    def test_other_containers_are_recognised_too(self) -> None:
        # Eram só `.mov` e `.mp4`: qualquer outro caía como link externo e abria
        # fora da área do aluno, sem preparo e sem proteção.
        for extension in (".mkv", ".avi", ".webm", ".wmv", ".m4v"):
            assert extension in VIDEO_EXTENSIONS

    def test_a_document_is_not_treated_as_video(self) -> None:
        for extension in (".pdf", ".md", ".zip", ".html"):
            assert extension not in VIDEO_EXTENSIONS



class TestDownloadSource:
    """O único ponto que fala com a rede, e onde o SSRF seria explorado.

    O endereço vem de dentro de um PDF que o vendedor enviou. Antes ele ia
    direto para o `ffmpeg`, que segue redirecionamento por conta própria: uma
    origem permitida respondendo `302` para um endereço interno passava por cima
    da allowlist inteira.
    """

    @staticmethod
    def _client(monkeypatch: pytest.MonkeyPatch, respostas: list[object]) -> list[str]:
        pedidos: list[str] = []

        class _Resposta:
            def __init__(self, item: object) -> None:
                if isinstance(item, tuple):
                    self.status_code, destino = item
                    self.headers = {"location": destino}
                    self._corpo: list[bytes] = []
                else:
                    self.status_code = 200
                    self.headers = {"etag": '"abc123"'}
                    self._corpo = [bytes(item)]

            async def __aenter__(self) -> _Resposta:
                return self

            async def __aexit__(self, *_: object) -> None:
                return None

            async def aiter_bytes(self, _size: int):
                for chunk in self._corpo:
                    # Cede o controle: é o que permite a um escopo de
                    # cancelamento externo interromper um download em curso.
                    await anyio.lowlevel.checkpoint()
                    yield chunk

        class _Cliente:
            def __init__(self, **kwargs: object) -> None:
                # Se estas duas sumirem, o cliente volta a seguir
                # redirecionamento sozinho e a ler proxy do ambiente — e a
                # revalidação de cada salto deixa de acontecer sem que nenhum
                # teste perceba.
                assert kwargs.get("follow_redirects") is False
                assert kwargs.get("trust_env") is False
                self._restantes = list(respostas)

            async def __aenter__(self) -> _Cliente:
                return self

            async def __aexit__(self, *_: object) -> None:
                return None

            def stream(
                self,
                _method: str,
                url: str,
                *,
                headers: dict[str, str],
                extensions: dict[str, str],
            ) -> _Resposta:
                # A conexão vai no IP validado, com o nome do host no TLS.
                assert headers["host"] == "midia.example.test"
                assert extensions["sni_hostname"] == "midia.example.test"
                pedidos.append(url)
                return _Resposta(self._restantes.pop(0))

        monkeypatch.setattr(pdf_link_video.httpx, "AsyncClient", _Cliente)
        return pedidos

    @staticmethod
    def _dns(monkeypatch: pytest.MonkeyPatch, endereco: str = "93.184.216.34") -> None:
        monkeypatch.setattr(
            pdf_link_video.socket,
            "getaddrinfo",
            lambda *_a, **_k: [(2, 1, 6, "", (endereco, 443))],
        )

    async def test_downloads_from_an_allowed_origin(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        self._dns(monkeypatch)
        pedidos = self._client(monkeypatch, [b"conteudo do video"])
        destino = tmp_path / "source.bin"
        assert await download_source(f"{ALLOWED}/aula.mov", destino) == (17, '"abc123"')
        assert destino.read_bytes() == b"conteudo do video"
        # Conectou no endereço validado, não no nome — sem uma segunda
        # resolução entre a checagem e a conexão.
        assert pedidos == ["https://93.184.216.34/aula.mov"]

    async def test_a_redirect_to_an_origin_outside_the_allowlist_is_refused(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # O ataque concreto: a origem permitida responde 302 para o serviço de
        # metadata da nuvem. O `ffmpeg` seguia sem perguntar; aqui cada salto é
        # revalidado contra a allowlist.
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        self._dns(monkeypatch)
        self._client(monkeypatch, [(302, "https://169.254.169.254/latest/meta-data")])
        with pytest.raises(PdfLinkVideoRejected):
            await download_source(f"{ALLOWED}/aula.mov", tmp_path / "source.bin")

    async def test_a_redirect_inside_the_allowlist_is_followed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        self._dns(monkeypatch)
        pedidos = self._client(monkeypatch, [(302, "/outra.mov"), b"conteudo"])
        assert (await download_source(f"{ALLOWED}/aula.mov", tmp_path / "source.bin"))[0] == 8
        assert pedidos == [
            "https://93.184.216.34/aula.mov",
            "https://93.184.216.34/outra.mov",
        ]

    async def test_endless_redirects_stop(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        self._dns(monkeypatch)
        self._client(monkeypatch, [(302, "/a.mov")] * 10)
        with pytest.raises(PdfLinkVideoRejected):
            await download_source(f"{ALLOWED}/aula.mov", tmp_path / "source.bin")

    @pytest.mark.parametrize(
        "endereco",
        ["127.0.0.1", "10.0.0.5", "169.254.169.254", "192.168.1.1", "::1", "fd00::1"],
    )
    async def test_a_host_resolving_inside_the_network_is_refused(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, endereco: str
    ) -> None:
        # A allowlist confere o nome; isto confere para onde o nome aponta.
        # Sem os dois, bastaria uma origem permitida apontar o DNS para dentro.
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        self._dns(monkeypatch, endereco)
        self._client(monkeypatch, [b"conteudo"])
        with pytest.raises(PdfLinkVideoRejected):
            await download_source(f"{ALLOWED}/aula.mov", tmp_path / "source.bin")

    async def test_a_body_larger_than_the_ceiling_stops_mid_download(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # O teto vale sobre os bytes lidos, não sobre o que a origem declara:
        # um `content-length` mentiroso não enche o disco.
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        monkeypatch.setattr(pdf_link_video, "MAX_SOURCE_BYTES", 8)
        self._dns(monkeypatch)
        self._client(monkeypatch, [b"muito maior que o teto"])
        with pytest.raises(PdfLinkVideoRejected):
            await download_source(f"{ALLOWED}/aula.mov", tmp_path / "source.bin")

    async def test_the_download_can_be_cancelled_from_outside(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # O prazo é um escopo de cancelamento em volta, e não um relógio olhado
        # entre pedaços: um relógio só é consultado quando chega um pedaço, e
        # uma origem que manda alguns bytes de vez em quando nunca chega lá.
        # Isto prova que o download cede o controle e pode ser interrompido.
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        self._dns(monkeypatch)
        self._client(monkeypatch, [b"conteudo"])
        with pytest.raises(TimeoutError):
            with anyio.fail_after(0):
                await download_source(f"{ALLOWED}/aula.mov", tmp_path / "source.bin")

    async def test_an_empty_body_is_refused(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        self._dns(monkeypatch)
        self._client(monkeypatch, [b""])
        with pytest.raises(PdfLinkVideoRejected):
            await download_source(f"{ALLOWED}/aula.mov", tmp_path / "source.bin")


class TestRejectionWindow:
    """Uma recusa não pode queimar o endereço para sempre."""

    def test_a_rejection_expires(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # O vendedor troca o arquivo por um bom; sem prazo, o aluno continuaria
        # sem vídeo para sempre porque a chave é a mesma.
        registry = PreparationRegistry()
        registry.reject("chave")
        assert registry.rejected("chave") is True
        # O relógio é adiantado, e não a suíte: esperar dez minutos por um
        # teste seria trocar uma espera do aluno por uma espera do CI.
        agora = time.monotonic()
        monkeypatch.setattr(
            pdf_link_video.time,
            "monotonic",
            lambda: agora + PreparationRegistry.REJECTION_SECONDS + 1,
        )
        assert registry.rejected("chave") is False
        assert registry.start("chave") is True


class _Cabecalhos:
    """Uma resposta com cabeçalhos escolhidos, para os casos sem `etag`."""

    def __init__(self, status: int, headers: dict) -> None:
        self.status = status
        self.headers = headers


class _Registro:
    """O que o dublê viu: os pedidos feitos e se alguém leu o corpo."""

    def __init__(self) -> None:
        self.pedidos: list[dict] = []
        self.lido = {"corpo": False}


class TestOriginValidator:
    """Nem toda resposta ruim da origem significa que o arquivo sumiu.

    A distinção decide se o aluno continua vendo o vídeo já preparado ou fica
    sem nada: só uma ausência declarada pela origem autoriza tirá-lo do ar.
    """

    @staticmethod
    def _client(monkeypatch: pytest.MonkeyPatch, respostas: list[object]) -> _Registro:
        registro = _Registro()
        pedidos = registro.pedidos
        lido = registro.lido

        class _Resposta:
            def __init__(self, item: object) -> None:
                if isinstance(item, _Cabecalhos):
                    self.status_code = item.status
                    self.headers = item.headers
                elif isinstance(item, tuple):
                    self.status_code, destino = item
                    self.headers = {"location": destino}
                else:
                    self.status_code = int(item)
                    self.headers = (
                        {"etag": '"abc123"'} if self.status_code in (200, 206) else {}
                    )

            async def __aenter__(self) -> _Resposta:
                return self

            async def __aexit__(self, *_: object) -> None:
                return None

            async def aiter_bytes(self, _size: int):
                # Uma origem que ignora o `Range` devolve o arquivo inteiro.
                # Se a pergunta de metadado ler isto, são centenas de megabytes
                # para saber um `etag`.
                lido["corpo"] = True
                yield b"x" * 1024

        class _Cliente:
            def __init__(self, **kwargs: object) -> None:
                assert kwargs.get("follow_redirects") is False
                assert kwargs.get("trust_env") is False
                self._restantes = list(respostas)

            async def __aenter__(self) -> _Cliente:
                return self

            async def __aexit__(self, *_: object) -> None:
                return None

            def stream(
                self,
                metodo: str,
                url: str,
                *,
                headers: dict[str, str],
                extensions: dict[str, str],
            ) -> _Resposta:
                assert headers["host"] == "midia.example.test"
                assert extensions["sni_hostname"] == "midia.example.test"
                pedidos.append({"metodo": metodo, "url": url, "headers": dict(headers)})
                item = self._restantes.pop(0)
                if isinstance(item, Exception):
                    raise item
                return _Resposta(item)

        monkeypatch.setattr(pdf_link_video.httpx, "AsyncClient", _Cliente)
        return registro

    @staticmethod
    def _dns(monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            pdf_link_video.socket,
            "getaddrinfo",
            lambda *_a, **_k: [(2, 1, 6, "", ("93.184.216.34", 443))],
        )

    async def test_reads_the_validator_without_touching_the_body(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        self._dns(monkeypatch)
        pedidos = self._client(monkeypatch, [200])
        assert await origin_validator(f"{ALLOWED}/aula.mov") == '"abc123"'
        assert pedidos.pedidos[0]["metodo"] == "HEAD"
        assert pedidos.pedidos[0]["url"] == "https://93.184.216.34/aula.mov"
        assert pedidos.lido["corpo"] is False

    @pytest.mark.parametrize("status", [404, 410])
    async def test_a_declared_absence_takes_the_video_down(
        self, monkeypatch: pytest.MonkeyPatch, status: int
    ) -> None:
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        self._dns(monkeypatch)
        self._client(monkeypatch, [status])
        with pytest.raises(pdf_link_video.PdfLinkVideoRejected):
            await origin_validator(f"{ALLOWED}/aula.mov")

    @pytest.mark.parametrize("status", [429, 500, 503, 403, 401, 418])
    async def test_an_origin_with_problems_does_not_take_the_video_down(
        self, monkeypatch: pytest.MonkeyPatch, status: int
    ) -> None:
        # Sobrecarga, erro do servidor ou autenticação não dizem que o arquivo
        # sumiu. Tratá-los como ausência tiraria o vídeo do aluno por um
        # problema alheio e passageiro.
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        self._dns(monkeypatch)
        self._client(monkeypatch, [status])
        with pytest.raises(pdf_link_video.PdfLinkVideoUnavailable):
            await origin_validator(f"{ALLOWED}/aula.mov")

    async def test_an_origin_that_refuses_head_is_asked_for_the_first_byte(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Recusar `HEAD` é comum e não diz nada sobre o arquivo existir.
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        self._dns(monkeypatch)
        pedidos = self._client(monkeypatch, [405, 206])
        assert await origin_validator(f"{ALLOWED}/aula.mov") == '"abc123"'
        assert [p["metodo"] for p in pedidos.pedidos] == ["HEAD", "GET"]
        assert pedidos.pedidos[1]["headers"]["range"] == "bytes=0-0"
        assert pedidos.lido["corpo"] is False

    async def test_a_redirect_after_the_fallback_is_revalidated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # O redirecionamento que vem depois do `GET` volta pelo mesmo laço, e
        # não escapa como falha: senão a allowlist deixaria de valer justamente
        # no caminho alternativo.
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        self._dns(monkeypatch)
        pedidos = self._client(monkeypatch, [405, (302, "/outra.mov"), 206])
        assert await origin_validator(f"{ALLOWED}/aula.mov") == '"abc123"'
        assert pedidos.pedidos[-1]["url"] == "https://93.184.216.34/outra.mov"

    async def test_a_redirect_out_of_the_allowlist_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        self._dns(monkeypatch)
        self._client(monkeypatch, [(302, "https://169.254.169.254/latest")])
        with pytest.raises(pdf_link_video.PdfLinkVideoRejected):
            await origin_validator(f"{ALLOWED}/aula.mov")

    async def test_a_network_failure_in_the_fallback_is_transient(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        self._dns(monkeypatch)
        self._client(monkeypatch, [405, pdf_link_video.httpx.ConnectError("caiu")])
        with pytest.raises(pdf_link_video.PdfLinkVideoUnavailable):
            await origin_validator(f"{ALLOWED}/aula.mov")

    async def test_a_range_answer_without_etag_uses_the_total_size(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # `content-length` num `206` é o tamanho do trecho — `1`. Compará-lo com
        # o tamanho gravado no download marcaria o derivado como velho a cada
        # acesso, e o remux se repetiria para sempre.
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        self._dns(monkeypatch)
        self._client(monkeypatch, [
            _Cabecalhos(206, {"content-length": "1", "content-range": "bytes 0-0/50000000"})
        ])
        assert await origin_validator(f"{ALLOWED}/aula.mov") == "50000000"

    async def test_a_whole_answer_without_etag_uses_the_length(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        self._dns(monkeypatch)
        self._client(monkeypatch, [_Cabecalhos(200, {"content-length": "50000000"})])
        assert await origin_validator(f"{ALLOWED}/aula.mov") == "50000000"

    @pytest.mark.parametrize(
        "cabecalhos",
        [{}, {"content-range": "bytes 0-0/*"}, {"content-length": "muitos"}],
    )
    async def test_an_origin_without_a_comparable_version_serves_the_cache(
        self, monkeypatch: pytest.MonkeyPatch, cabecalhos: dict
    ) -> None:
        # Sem nada comparável, dizer qualquer coisa marcaria o derivado como
        # velho para sempre. "Não sei" serve o que já existe.
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        self._dns(monkeypatch)
        self._client(monkeypatch, [_Cabecalhos(200, cabecalhos)])
        with pytest.raises(pdf_link_video.PdfLinkVideoUnavailable):
            await origin_validator(f"{ALLOWED}/aula.mov")

    async def test_four_redirects_stop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        self._dns(monkeypatch)
        self._client(monkeypatch, [(302, "/a.mov")] * 6)
        with pytest.raises(pdf_link_video.PdfLinkVideoRejected):
            await origin_validator(f"{ALLOWED}/aula.mov")

    async def test_the_method_swap_does_not_spend_a_redirect(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Trocar de método não é um salto na corrente de endereços; contá-lo
        # como tal faria o teto valer para outra coisa.
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        self._dns(monkeypatch)
        self._client(monkeypatch, [
            (302, "/a.mov"), (302, "/b.mov"), (302, "/c.mov"), 405, 206
        ])
        assert await origin_validator(f"{ALLOWED}/aula.mov") == '"abc123"'

    async def test_the_fallback_without_etag_uses_the_total_from_the_range(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # O caminho completo: a origem recusa `HEAD`, responde ao trecho, e não
        # tem `etag`. É o cenário em que confundir `content-length` com tamanho
        # do arquivo faria o remux se repetir a cada acesso.
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        self._dns(monkeypatch)
        self._client(monkeypatch, [
            405,
            _Cabecalhos(206, {"content-length": "1", "content-range": "bytes 0-0/50000000"}),
        ])
        assert await origin_validator(f"{ALLOWED}/aula.mov") == "50000000"

    @pytest.mark.parametrize(
        "cabecalhos",
        [
            {"content-length": "1"},
            {"content-length": "1", "content-range": "qualquer-coisa/50000000"},
            {"content-length": "1", "content-range": "bytes 0-0/*"},
            {"content-range": "bytes 0-0/0"},
        ],
    )
    async def test_a_partial_answer_never_falls_back_to_the_length(
        self, monkeypatch: pytest.MonkeyPatch, cabecalhos: dict
    ) -> None:
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        self._dns(monkeypatch)
        self._client(monkeypatch, [_Cabecalhos(206, cabecalhos)])
        with pytest.raises(pdf_link_video.PdfLinkVideoUnavailable):
            await origin_validator(f"{ALLOWED}/aula.mov")

    async def test_a_whole_answer_with_a_range_is_not_comparable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # `200` com `content-range` é resposta contraditória; usar o total dali
        # seria adivinhar sobre uma origem que não está se explicando.
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        self._dns(monkeypatch)
        self._client(monkeypatch, [
            _Cabecalhos(200, {"content-length": "1", "content-range": "bytes 0-0/50000000"})
        ])
        with pytest.raises(pdf_link_video.PdfLinkVideoUnavailable):
            await origin_validator(f"{ALLOWED}/aula.mov")
