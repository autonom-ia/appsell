"""A decisão de copiar ou recodificar, e os tetos que valem sobre a origem.

Este módulo roda dentro do `pdf-sandbox`, sobre um arquivo que o vendedor
escolheu. `test_parser_de_midia_fica_no_sandbox.py` é quem garante que ele
continue lá.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from appsell_api import video_remux
from appsell_api.video_remux import (
    ALLOWED_FORMATS,
    MAX_DURATION_SECONDS,
    MAX_SOURCE_FPS,
    MAX_SOURCE_PIXELS,
    VideoRemuxRejected,
    copyable_streams,
    remux_to_mp4,
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


def _ffmpeg() -> str:
    caminho = shutil.which("ffmpeg")
    assert caminho is not None, "ffmpeg é dependência destes testes; o CI o instala"
    return caminho


def _fonte(tmp_path: Path, *, segundos: int = 10, codec: str = "libx264") -> Path:
    """Um vídeo de verdade, gerado pelo próprio ffmpeg.

    Metadado inventado não serve aqui: o que se quer provar é o comportamento do
    `ffmpeg` sobre um arquivo, não o do nosso parser sobre um dicionário.
    """
    destino = tmp_path / f"origem-{codec}.mp4"
    subprocess.run(  # noqa: S603 - argv fixo, gerado pelo próprio teste
        [
            _ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={segundos}:size=320x240:rate=30",
            "-c:v",
            codec,
            "-pix_fmt",
            "yuv420p",
            str(destino),
        ],
        check=True,
        capture_output=True,
    )
    return destino


class TestSaidaTruncada:
    """`ffmpeg -fs` corta a saída no teto de bytes e **sai com código zero**.

    O arquivo curto tem tamanho válido e código de saída de sucesso, então nada
    no caminho normal acusa. O aluno recebe a aula pela metade. Quem acusa é a
    duração.
    """

    def test_uma_saida_mais_curta_que_a_origem_e_recusada(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A guarda isolada: a saída dura menos que a origem, e isso basta.

        A origem declara dez minutos e o arquivo tem dois segundos. É a forma
        exata do defeito que o `-fs` produz, sem depender de o teto de bytes
        cair no lugar certo para provocá-lo.
        """
        origem = _fonte(tmp_path, segundos=2)
        real = video_remux.probe_video

        def alongando_a_origem(caminho: Path) -> dict[str, object]:
            lido = real(caminho)
            if caminho == origem:
                lido["format"]["duration"] = "600.0"
            return lido

        monkeypatch.setattr(video_remux, "probe_video", alongando_a_origem)
        with pytest.raises(VideoRemuxRejected, match="shorter"):
            remux_to_mp4(origem, tmp_path / "curto.mp4")

    def test_o_corte_por_teto_de_bytes_nao_vira_arquivo_aceito(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """O caso de ponta a ponta: com o teto apertado, nada sai aprovado.

        Qual das duas guardas dispara depende de o `ffmpeg` parar um pouco antes
        ou um pouco depois do teto, e isso não é nosso para controlar. O que
        importa é que nenhum dos dois desfechos entrega um vídeo cortado.
        """
        origem = _fonte(tmp_path, segundos=10)
        monkeypatch.setattr(video_remux, "MAX_DERIVATIVE_BYTES", 20_000)
        with pytest.raises(VideoRemuxRejected):
            remux_to_mp4(origem, tmp_path / "curto.mp4")

    def test_a_saida_inteira_passa(self, tmp_path: Path) -> None:
        origem = _fonte(tmp_path, segundos=2)
        destino = tmp_path / "inteiro.mp4"
        assert remux_to_mp4(origem, destino) is False
        assert destino.stat().st_size > 0


class TestModoSemRecodificar:
    """Quem tem prazo curto pede a troca de invólucro, e recusa o resto.

    Recodificar custa 66 s por minuto de vídeo. O worker do catálogo tem lease
    de 600 s: uma aula de 10 minutos estoura. Trocar o invólucro custa 0,155 s
    por minuto, e é a única coisa que cabe.
    """

    def test_uma_origem_que_precisa_recodificar_e_recusada(self, tmp_path: Path) -> None:
        origem = _fonte(tmp_path, segundos=2, codec="mpeg4")
        with pytest.raises(VideoRemuxRejected, match="transcod"):
            remux_to_mp4(origem, tmp_path / "saida.mp4", allow_transcode=False)

    def test_uma_origem_copiavel_passa_sem_recodificar(self, tmp_path: Path) -> None:
        origem = _fonte(tmp_path, segundos=2)
        destino = tmp_path / "saida.mp4"
        assert remux_to_mp4(origem, destino, allow_transcode=False) is False

    def test_o_indice_do_mp4_fica_no_comeco(self, tmp_path: Path) -> None:
        """`+faststart` é o ponto de tudo isto: sem ele o aluno não arrasta a barra."""
        origem = _fonte(tmp_path, segundos=2)
        destino = tmp_path / "saida.mp4"
        remux_to_mp4(origem, destino, allow_transcode=False)
        conteudo = destino.read_bytes()
        assert conteudo.index(b"moov") < conteudo.index(b"mdat")
