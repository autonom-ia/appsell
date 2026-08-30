"""O vídeo do PDF vira MP4, e só busca o que a allowlist permite."""

from __future__ import annotations

from pathlib import Path

import pytest

from appsell_api import pdf_link_video
from appsell_api.pdf_link_video import (
    MAX_DURATION_SECONDS,
    PdfLinkVideoRejected,
    PreparationRegistry,
    copyable_streams,
    derivative_key,
    download_source,
    trusted_video_target,
)
from appsell_api.pdf_links import VIDEO_EXTENSIONS

ALLOWED = "https://midia.example.test"


SELLER = "aea20a25-9889-47a5-bdbf-8588aae3ba1c"
OUTRO_SELLER = "1c2d3e4f-5678-4abc-8def-9012345678ab"


def _probe(
    *,
    duration: float = 30.0,
    video_codec: str | None = "h264",
    audio_codec: str | None = "aac",
    pix_fmt: str = "yuv420p",
    extra_video: bool = False,
) -> dict[str, object]:
    streams: list[dict[str, object]] = []
    if video_codec is not None:
        streams.append(
            {"codec_type": "video", "codec_name": video_codec, "pix_fmt": pix_fmt}
        )
    if extra_video:
        streams.append({"codec_type": "video", "codec_name": "h264", "pix_fmt": pix_fmt})
    if audio_codec is not None:
        streams.append({"codec_type": "audio", "codec_name": audio_codec})
    return {"format": {"duration": str(duration)}, "streams": streams}


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


class TestPlan:
    """Copiar quando dá, recodificar quando é preciso, recusar o impossível."""

    def test_h264_with_aac_only_changes_container(self) -> None:
        assert copyable_streams(_probe()) is True

    def test_mp3_audio_is_also_copyable(self) -> None:
        assert copyable_streams(_probe(audio_codec="mp3")) is True

    def test_video_without_audio_is_copyable(self) -> None:
        assert copyable_streams(_probe(audio_codec=None)) is True

    @pytest.mark.parametrize("codec", ["prores", "hevc", "vp9", "mpeg4"])
    def test_a_codec_the_browser_cannot_play_is_recoded(self, codec: str) -> None:
        assert copyable_streams(_probe(video_codec=codec)) is False

    def test_audio_the_browser_cannot_play_is_recoded(self) -> None:
        assert copyable_streams(_probe(audio_codec="pcm_s16le")) is False

    def test_more_than_one_video_track_is_recoded(self) -> None:
        assert copyable_streams(_probe(extra_video=True)) is False

    def test_a_source_without_video_is_refused(self) -> None:
        with pytest.raises(PdfLinkVideoRejected):
            copyable_streams(_probe(video_codec=None))

    def test_a_source_longer_than_the_ceiling_is_refused(self) -> None:
        with pytest.raises(PdfLinkVideoRejected):
            copyable_streams(_probe(duration=MAX_DURATION_SECONDS + 1))

    def test_a_source_with_no_duration_is_refused(self) -> None:
        with pytest.raises(PdfLinkVideoRejected):
            copyable_streams(_probe(duration=0))

    def test_metadata_that_is_not_numeric_is_refused(self) -> None:
        with pytest.raises(PdfLinkVideoRejected):
            copyable_streams({"format": {"duration": "muito"}, "streams": []})

    @pytest.mark.parametrize("pix_fmt", ["yuv420p10le", "yuv422p", "yuv444p"])
    def test_h264_in_a_variant_the_browser_cannot_draw_is_recoded(self, pix_fmt: str) -> None:
        # H.264 de 10 bits ou 4:2:2 é H.264, e mesmo assim o navegador não
        # desenha. Copiar entregaria um MP4 tão inútil quanto o original.
        assert copyable_streams(_probe(pix_fmt=pix_fmt)) is False


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
                    self.headers = {}
                    self._corpo = [bytes(item)] if isinstance(item, (bytes, bytearray)) else [item]

            def __enter__(self) -> _Resposta:
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def iter_bytes(self, _size: int) -> list[bytes]:
                return self._corpo

        class _Cliente:
            def __init__(self, **_: object) -> None:
                self._restantes = list(respostas)

            def __enter__(self) -> _Cliente:
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def stream(self, _method: str, url: str) -> _Resposta:
                pedidos.append(url)
                return _Resposta(self._restantes.pop(0))

        monkeypatch.setattr(pdf_link_video.httpx, "Client", _Cliente)
        return pedidos

    @staticmethod
    def _dns(monkeypatch: pytest.MonkeyPatch, endereco: str = "93.184.216.34") -> None:
        monkeypatch.setattr(
            pdf_link_video.socket,
            "getaddrinfo",
            lambda *_a, **_k: [(2, 1, 6, "", (endereco, 443))],
        )

    def test_downloads_from_an_allowed_origin(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        self._dns(monkeypatch)
        self._client(monkeypatch, [b"conteudo do video"])
        destino = tmp_path / "source.bin"
        assert download_source(f"{ALLOWED}/aula.mov", destino) == 17
        assert destino.read_bytes() == b"conteudo do video"

    def test_a_redirect_to_an_origin_outside_the_allowlist_is_refused(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # O ataque concreto: a origem permitida responde 302 para o serviço de
        # metadata da nuvem. O `ffmpeg` seguia sem perguntar; aqui cada salto é
        # revalidado contra a allowlist.
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        self._dns(monkeypatch)
        self._client(monkeypatch, [(302, "https://169.254.169.254/latest/meta-data")])
        with pytest.raises(PdfLinkVideoRejected):
            download_source(f"{ALLOWED}/aula.mov", tmp_path / "source.bin")

    def test_a_redirect_inside_the_allowlist_is_followed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        self._dns(monkeypatch)
        pedidos = self._client(monkeypatch, [(302, "/outra.mov"), b"conteudo"])
        assert download_source(f"{ALLOWED}/aula.mov", tmp_path / "source.bin") == 8
        assert pedidos == [f"{ALLOWED}/aula.mov", f"{ALLOWED}/outra.mov"]

    def test_endless_redirects_stop(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        self._dns(monkeypatch)
        self._client(monkeypatch, [(302, "/a.mov")] * 10)
        with pytest.raises(PdfLinkVideoRejected):
            download_source(f"{ALLOWED}/aula.mov", tmp_path / "source.bin")

    @pytest.mark.parametrize(
        "endereco",
        ["127.0.0.1", "10.0.0.5", "169.254.169.254", "192.168.1.1", "::1", "fd00::1"],
    )
    def test_a_host_resolving_inside_the_network_is_refused(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, endereco: str
    ) -> None:
        # A allowlist confere o nome; isto confere para onde o nome aponta.
        # Sem os dois, bastaria uma origem permitida apontar o DNS para dentro.
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        self._dns(monkeypatch, endereco)
        self._client(monkeypatch, [b"conteudo"])
        with pytest.raises(PdfLinkVideoRejected):
            download_source(f"{ALLOWED}/aula.mov", tmp_path / "source.bin")

    def test_a_body_larger_than_the_ceiling_stops_mid_download(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # O teto vale sobre os bytes lidos, não sobre o que a origem declara:
        # um `content-length` mentiroso não enche o disco.
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        monkeypatch.setattr(pdf_link_video, "MAX_SOURCE_BYTES", 8)
        self._dns(monkeypatch)
        self._client(monkeypatch, [b"muito maior que o teto"])
        with pytest.raises(PdfLinkVideoRejected):
            download_source(f"{ALLOWED}/aula.mov", tmp_path / "source.bin")

    def test_an_empty_body_is_refused(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", ALLOWED)
        self._dns(monkeypatch)
        self._client(monkeypatch, [b""])
        with pytest.raises(PdfLinkVideoRejected):
            download_source(f"{ALLOWED}/aula.mov", tmp_path / "source.bin")
