import asyncio
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from test_config import INTERNAL_AUTH, REMOTE_STORAGE

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


@pytest.mark.asyncio
async def test_a_api_sobe_com_o_sandbox_fora_e_o_ready_denuncia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O `lifespan` inteiro, com o serviço isolado fora.

    Duas coisas ao mesmo tempo, e as duas importam:

    - a API **sobe**. Ela atende muita coisa que não passa pelo serviço isolado,
      e não há ordem garantida entre os dois no arranque. Morrer aqui trocaria
      "o leitor de PDF está fora" por "a API inteira está fora".
    - o `ready` **reprova**. Era isso que faltava em 30/08: o socket sumiu e o
      `ready` respondeu que estava tudo bem o tempo inteiro.

    O teste passa pelo `lifespan` de ponta a ponta de propósito. Uma versão que
    só olhasse o trecho da sonda não veria a linha, mais abaixo, que marcava o
    sandbox como pronto junto com o storage e desfazia tudo.
    """
    sondado: list[bool] = []

    async def _sandbox_fora(_settings: object) -> bool:
        sondado.append(True)
        return False

    monkeypatch.setattr(main, "_pdf_sandbox_ready", _sandbox_fora)
    monkeypatch.setattr(main, "get_settings", lambda: _producao_com_sandbox())
    monkeypatch.setattr(
        main,
        "create_engine",
        # O motor é de verdade, mas nunca conecta: quem responde pelo banco
        # neste teste é `database_ready`, e o assunto aqui é o sandbox.
        lambda s: create_async_engine(s.database_url),
    )
    monkeypatch.setattr(main, "S3Storage", lambda _s: _StorageFalso())
    # O storage não é o assunto: o que se mede aqui é o sandbox.
    monkeypatch.setattr(main, "assert_storage_worker_ready", lambda **_kwargs: None)
    # O laço de fulfillment conversa com o banco, e o banco não é o assunto.
    monkeypatch.setattr(main, "run_fulfillment_worker_loop", _nunca_termina)
    monkeypatch.setattr(main, "database_ready", _sempre(True))
    monkeypatch.setattr(main, "_storage_ready", _sempre(True))

    async with main.lifespan(main.app):
        assert sondado == [True], "a sonda do sandbox não chegou a rodar"
        # A subida terminou — a API está de pé.
        assert main.app.state.pdf_sandbox_runtime_ready is False

        transport = ASGITransport(app=main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["pdf_sandbox"] is False


def _sempre(valor: object):  # type: ignore[no-untyped-def]
    async def _responde(*_args: object, **_kwargs: object) -> object:
        return valor

    return _responde


def _producao_com_sandbox() -> Any:
    """As configurações do caso que importa: produção falando com o sandbox."""
    from appsell_api.config import Settings

    remoto = {**REMOTE_STORAGE, "pdf_sandbox_url": ""}
    return Settings(
        environment="production",
        database_url="postgresql+asyncpg://runtime@db.example.test:5432/appsell?ssl=require",
        pdf_sandbox_socket="/run/appsell-pdf/pdf.sock",
        **INTERNAL_AUTH,
        **remoto,
    )


class _StorageFalso:
    """O storage não é o assunto deste teste, e não pode ser o que o quebra."""

    def ensure_local_ci_controls(self, **_kwargs: object) -> None:
        return None


async def _nunca_termina(*_args: object, **_kwargs: object) -> None:
    """Fica parado, como o laço de verdade — sem tocar em banco nenhum."""
    await asyncio.Event().wait()


def test_o_healthcheck_do_container_consome_o_ready() -> None:
    """O `ready` não é só informação: é ele que decide se a task vive.

    O `HEALTHCHECK` da imagem chama `/health/ready`. Somar o sandbox a esse
    endereço tem uma consequência operacional que precisa estar escrita: com o
    serviço isolado fora, a API passa a ser derrubada e substituída.

    A tolerância é o que torna isso aceitável — `interval=10s` e `retries=6`
    dão um minuto antes de a task cair, e `start-period` cobre a corrida do
    arranque. Um sandbox que volta em menos de um minuto não derruba nada; um
    que não volta derruba, e é o que se quer num deploy: foi a promoção
    silenciosa de uma task quebrada que fez todo PDF abrir sem botão.

    Este teste existe para a troca do endereço por `/health/live` ser uma
    decisão, e não um efeito colateral de alguém mexendo no Dockerfile.
    """
    from pathlib import Path as _Path

    dockerfile = _Path(__file__).resolve().parents[3] / "deploy/easypanel-native/Dockerfile.api"
    conteudo = dockerfile.read_text()
    healthcheck = conteudo[conteudo.index("HEALTHCHECK") :]
    healthcheck = healthcheck[: healthcheck.index("\nCMD [")]

    assert "/health/ready" in healthcheck
    assert "--retries=6" in healthcheck
    assert "--interval=10s" in healthcheck
