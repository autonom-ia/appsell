"""O vídeo do PDF vira MP4, e só busca o que a allowlist permite."""

from __future__ import annotations

import pytest

from appsell_api.pdf_link_video import (
    MAX_DURATION_SECONDS,
    MAX_SOURCE_BYTES,
    PdfLinkVideoRejected,
    PreparationRegistry,
    copyable_streams,
    derivative_key,
    trusted_video_target,
)
from appsell_api.pdf_links import VIDEO_EXTENSIONS

ALLOWED = "https://midia.example.test"


def _probe(
    *,
    duration: float = 30.0,
    size: int = 5 * 1024 * 1024,
    video_codec: str | None = "h264",
    audio_codec: str | None = "aac",
    extra_video: bool = False,
) -> dict[str, object]:
    streams: list[dict[str, object]] = []
    if video_codec is not None:
        streams.append({"codec_type": "video", "codec_name": video_codec})
    if extra_video:
        streams.append({"codec_type": "video", "codec_name": "h264"})
    if audio_codec is not None:
        streams.append({"codec_type": "audio", "codec_name": audio_codec})
    return {"format": {"duration": str(duration), "size": str(size)}, "streams": streams}


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
        assert derivative_key("https://a.test/x.mov") == derivative_key("https://a.test/x.mov")

    def test_different_addresses_do_not_collide(self) -> None:
        assert derivative_key("https://a.test/x.mov") != derivative_key("https://a.test/y.mov")

    def test_the_key_carries_no_characters_from_the_address(self) -> None:
        # A chave é um resumo, então nem o host nem o caminho da origem ficam
        # legíveis no nome do objeto guardado.
        key = derivative_key("https://midia.example.test/pasta secreta/aula.mov")
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

    def test_a_source_larger_than_the_ceiling_is_refused(self) -> None:
        with pytest.raises(PdfLinkVideoRejected):
            copyable_streams(_probe(size=MAX_SOURCE_BYTES + 1))

    def test_a_source_with_no_duration_is_refused(self) -> None:
        with pytest.raises(PdfLinkVideoRejected):
            copyable_streams(_probe(duration=0))

    def test_metadata_that_is_not_numeric_is_refused(self) -> None:
        with pytest.raises(PdfLinkVideoRejected):
            copyable_streams({"format": {"duration": "muito", "size": "grande"}, "streams": []})


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
