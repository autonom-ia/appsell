from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from appsell_api import main


@pytest.mark.asyncio
async def test_liveness_does_not_require_database() -> None:
    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_o_ready_denuncia_o_sandbox_fora_em_vez_de_dizer_que_esta_tudo_bem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O `ready` responde 503 quando o serviço isolado não atende.

    Em 30/08/2026 o socket do serviço sumiu num deploy e todo PDF passou a abrir
    sem botão. O `ready` respondeu `{"pdf_sandbox": true}` o tempo inteiro,
    porque em produção o sandbox não contava como requisito e a sonda devolvia
    verdadeiro sem sondar nada. Foi por isso que eu reverti a imagem, vi os
    serviços de pé e anunciei que estava resolvido enquanto o produto seguia
    quebrado.
    """
    monkeypatch.setattr(main.app.state, "db_engine", None, raising=False)
    monkeypatch.setattr(main, "database_ready", _sempre(True))
    monkeypatch.setattr(main, "_storage_ready", _sempre(True))
    monkeypatch.setattr(main, "_cached_dependency_ready", lambda *_args, **_kwargs: False)

    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/ready")

    assert response.status_code == 503
    corpo = response.json()
    assert corpo["status"] == "unavailable"
    assert corpo["pdf_sandbox"] is False


def test_a_api_sobe_mesmo_com_o_sandbox_fora() -> None:
    """Subir é uma coisa; estar pronta é outra.

    A API atende muita coisa que não passa pelo serviço isolado, e não há ordem
    garantida entre os dois no arranque. Morrer no boot trocaria "o leitor de
    PDF está fora" por "a API inteira está fora", com um laço de reinício até o
    outro aparecer. Quem precisa do sandbox para existir é o worker, e é ele
    quem recusa subir sem — decisão dele, no boot dele.
    """
    fonte = Path(main.__file__).read_text()
    arranque = fonte[fonte.index("    if settings.pdf_sandbox_required:") :]
    arranque = arranque[: arranque.index("\n    if settings.storage_configured:")]

    assert "raise RuntimeError" not in arranque
    assert "app.state.pdf_sandbox_runtime_ready = await _pdf_sandbox_ready(settings)" in arranque


def _sempre(valor: object):  # type: ignore[no-untyped-def]
    async def _responde(*_args: object, **_kwargs: object) -> object:
        return valor

    return _responde
