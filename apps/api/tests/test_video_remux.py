"""A decisão de copiar ou recodificar, e os tetos que valem sobre a origem.

Este módulo roda dentro do `pdf-sandbox`, sobre um arquivo que o vendedor
escolheu. `test_parser_de_midia_fica_no_sandbox.py` é quem garante que ele
continue lá.
"""

from __future__ import annotations

import pytest

from appsell_api.video_remux import (
    ALLOWED_FORMATS,
    MAX_DURATION_SECONDS,
    MAX_SOURCE_FPS,
    MAX_SOURCE_PIXELS,
    VideoRemuxRejected,
    copyable_streams,
)


def _probe(
    *,
    duration: float = 30.0,
    video_codec: str | None = "h264",
    audio_codec: str | None = "aac",
    pix_fmt: str = "yuv420p",
    width: int = 1920,
    height: int = 1080,
    frame_rate: str = "30/1",
    extra_video: bool = False,
) -> dict[str, object]:
    streams: list[dict[str, object]] = []
    if video_codec is not None:
        streams.append(
            {
                "codec_type": "video",
                "codec_name": video_codec,
                "pix_fmt": pix_fmt,
                "width": width,
                "height": height,
                "r_frame_rate": frame_rate,
            }
        )
    if extra_video:
        streams.append(
            {
                "codec_type": "video",
                "codec_name": "h264",
                "pix_fmt": pix_fmt,
                "width": width,
                "height": height,
                "r_frame_rate": frame_rate,
            }
        )
    if audio_codec is not None:
        streams.append({"codec_type": "audio", "codec_name": audio_codec})
    return {"format": {"duration": str(duration)}, "streams": streams}


class TestPlan:
    """Copiar quando dá, recodificar quando é preciso, recusar o impossível."""

    def test_h264_with_aac_only_changes_container(self) -> None:
        # O caso do vendedor: QuickTime por fora, H.264 Main + AAC por dentro.
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

    @pytest.mark.parametrize("pix_fmt", ["yuv420p10le", "yuv422p", "yuv444p"])
    def test_h264_in_a_variant_the_browser_cannot_draw_is_recoded(self, pix_fmt: str) -> None:
        # H.264 de 10 bits ou 4:2:2 é H.264, e mesmo assim o navegador não
        # desenha. Copiar entregaria um MP4 tão inútil quanto o original.
        assert copyable_streams(_probe(pix_fmt=pix_fmt)) is False


class TestCeilings:
    """Os tetos valem sobre a origem, e não sobre o que sai."""

    def test_a_source_without_video_is_refused(self) -> None:
        with pytest.raises(VideoRemuxRejected):
            copyable_streams(_probe(video_codec=None))

    def test_a_source_longer_than_the_ceiling_is_refused(self) -> None:
        with pytest.raises(VideoRemuxRejected):
            copyable_streams(_probe(duration=MAX_DURATION_SECONDS + 1))

    def test_a_source_with_no_duration_is_refused(self) -> None:
        with pytest.raises(VideoRemuxRejected):
            copyable_streams(_probe(duration=0))

    def test_metadata_that_is_not_numeric_is_refused(self) -> None:
        with pytest.raises(VideoRemuxRejected):
            copyable_streams({"format": {"duration": "muito"}, "streams": []})

    def test_a_frame_larger_than_the_ceiling_is_refused(self) -> None:
        # A redução para 1080p acontece depois de decodificar. Sem teto aqui, um
        # quadro absurdo é alocado inteiro antes de qualquer redução.
        with pytest.raises(VideoRemuxRejected):
            copyable_streams(_probe(width=MAX_SOURCE_PIXELS, height=2))

    def test_a_source_without_dimensions_is_refused(self) -> None:
        with pytest.raises(VideoRemuxRejected):
            copyable_streams(_probe(width=0, height=0))

    def test_a_frame_rate_above_the_ceiling_is_refused(self) -> None:
        with pytest.raises(VideoRemuxRejected):
            copyable_streams(_probe(frame_rate=f"{MAX_SOURCE_FPS + 1}/1"))

    def test_a_fractional_frame_rate_is_read_correctly(self) -> None:
        # `30000/1001` é 29,97 — ler como 30000 recusaria vídeo comum.
        assert copyable_streams(_probe(frame_rate="30000/1001")) is True


class TestAllowedFormats:
    """Formatos que apontam para outros recursos ficam de fora."""

    @pytest.mark.parametrize("formato", ["hls", "dash", "concat", "applehttp"])
    def test_formats_that_reference_other_resources_are_absent(self, formato: str) -> None:
        # É por eles que um parser de mídia vira um cliente de rede.
        assert formato not in ALLOWED_FORMATS.split(",")

    def test_the_containers_the_seller_actually_uses_are_present(self) -> None:
        for formato in ("mov", "mp4", "matroska", "webm", "avi"):
            assert formato in ALLOWED_FORMATS.split(",")


class TestNonFiniteMetadata:
    """`NaN` falha toda comparação, e por isso passaria por qualquer faixa."""

    @pytest.mark.parametrize("duracao", ["nan", "inf", "-nan"])
    def test_a_duration_that_is_not_a_finite_number_is_refused(self, duracao: str) -> None:
        with pytest.raises(VideoRemuxRejected):
            copyable_streams(_probe(duration=float(duracao)))

    @pytest.mark.parametrize("taxa", ["nan/1", "0/0", "1/0", "-30/1"])
    def test_a_frame_rate_that_is_not_a_positive_number_is_refused(self, taxa: str) -> None:
        # `0/0` chega de arquivo sem taxa declarada. Tratar como zero e aceitar
        # deixaria passar exatamente o que o teto existe para barrar.
        with pytest.raises(VideoRemuxRejected):
            copyable_streams(_probe(frame_rate=taxa))
