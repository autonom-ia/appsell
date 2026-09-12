import asyncio
from contextlib import contextmanager
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from test_config import INTERNAL_AUTH, REMOTE_STORAGE

from appsell_api import main
from appsell_api.storage_s3 import StorageCapabilityError


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


@pytest.mark.asyncio
async def test_a_api_sobe_com_o_scanner_reprovado_e_o_ready_denuncia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O `lifespan` inteiro, com o antivírus reprovado.

    Em 12/09/2026 o `clamd` estava de pé e respondendo `PONG`, com a assinatura
    mais recente que existia publicada. O `healthy()` reprovou assim mesmo,
    porque mede a idade da assinatura — que é calendário do fornecedor — e ela
    tinha passado de 48h. O `lifespan` tratava isso como fatal, então a API não
    subiu, o Swarm religou, e a coisa virou crashloop: `sell.autonomia.site`
    ficou 3h06 em 502. Login, catálogo, checkout e leitura de PDF já validado
    caíram junto, e nenhum deles passa perto do antivírus.

    O antivírus é dependência do caminho de upload. Derrubar o processo inteiro
    por causa dele troca "não dá para enviar arquivo agora" por "a loja saiu do
    ar" — e o `_monitor_storage_dependencies`, que já roda, já reavalia isso
    sozinho e já devolve 503 no `ready` enquanto durar.
    """
    monkeypatch.setattr(main, "get_settings", lambda: _producao_com_storage())
    monkeypatch.setattr(main, "create_engine", lambda s: create_async_engine(s.database_url))
    monkeypatch.setattr(main, "S3Storage", lambda _s: _StorageFalso())
    monkeypatch.setattr(main, "run_fulfillment_worker_loop", _nunca_termina)
    monkeypatch.setattr(main, "database_ready", _sempre(True))
    # O monitor contínuo é quem passa a mandar: aqui ele nunca aprova o storage.
    monkeypatch.setattr(main, "_refresh_storage_dependencies", _sempre(None))

    def _scanner_reprovado(**_kwargs: object) -> None:
        raise StorageCapabilityError("malware scanner is unhealthy")

    monkeypatch.setattr(main, "assert_storage_worker_ready", _scanner_reprovado)

    async with main.lifespan(main.app):
        # A subida terminou — a API está de pé, que é o ponto.
        assert main.app.state.storage_runtime_ready is False

        transport = ASGITransport(app=main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            vivo = await client.get("/health/live")
            pronto = await client.get("/health/ready")

    assert vivo.status_code == 200, "a API precisa atender enquanto só o antivírus está fora"
    assert pronto.status_code == 503
    assert pronto.json()["storage"] is False


@pytest.mark.asyncio
async def test_o_startup_reprova_enquanto_a_task_nunca_ficou_pronta() -> None:
    """Task nova que nunca funcionou não pode ser promovida num deploy.

    É a metade do contrato que vem de 30/08/2026: o socket do sandbox sumiu num
    deploy, a task subiu quebrada, foi promovida em silêncio e todo PDF passou a
    abrir sem botão. Enquanto a task não provar uma vez que serve, o `startup`
    reprova e o Swarm não a promove.
    """
    main.app.state.ever_ready = False

    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with _ready_respondendo(False):
            response = await client.get("/health/startup")

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_o_startup_aprova_depois_de_ter_ficado_pronta_uma_vez() -> None:
    """Task que já serviu não morre por dependência que degradou depois.

    É a outra metade, e é a que faltava em 12/09/2026: o `HEALTHCHECK` sondava o
    `ready`, que agrega dependências externas, então uma dependência degradada
    em regime matava um processo saudável. Com `retries=6` e `interval=10s`, um
    minuto de antivírus fora bastava para o Swarm derrubar a task — e a próxima
    subia no mesmo estado, sem fim.

    Depois que a task provou que serve, quem reporta degradação é o `ready`.
    O `startup` passa a responder pelo processo, e só por ele.
    """
    main.app.state.ever_ready = False

    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with _ready_respondendo(True):
            primeira = await client.get("/health/startup")
        with _ready_respondendo(False):
            depois = await client.get("/health/startup")

    assert primeira.status_code == 200
    assert depois.status_code == 200, "dependência degradada não pode matar task que já serviu"
    assert main.app.state.ever_ready is True


def test_o_healthcheck_dos_containers_usa_o_startup() -> None:
    """A troca de `/health/ready` por `/health/startup` é decisão, não acidente.

    O teste anterior exigia `/health/ready` aqui, e o texto dele pedia que a
    troca fosse deliberada. Está sendo: o `ready` agrega dependências externas,
    e usá-lo como `HEALTHCHECK` de container faz o Swarm matar processo saudável
    quando uma dependência degrada — foi o que prolongou o 502 de 12/09/2026 e o
    que derrubou o `web` junto, já que o `ready` dele consulta a API.

    O `startup` preserva o que o endereço antigo protegia: enquanto a task nunca
    ficou pronta, ele espelha o `ready`, então task nova e quebrada segue sem ser
    promovida. A tolerância continua a mesma — `interval=10s` e `retries=6`.
    """
    from pathlib import Path as _Path

    raiz = _Path(__file__).resolve().parents[3]
    for nome, endereco in (
        ("Dockerfile.api", "/health/startup"),
        ("Dockerfile.web", "/api/health/startup"),
    ):
        conteudo = (raiz / "deploy/easypanel-native" / nome).read_text()
        healthcheck = conteudo[conteudo.index("HEALTHCHECK") :]
        healthcheck = healthcheck[: healthcheck.index("\nCMD [")]

        assert endereco in healthcheck, nome
        assert "/health/ready" not in healthcheck, nome
        assert "--retries=6" in healthcheck, nome
        assert "--interval=10s" in healthcheck, nome


def _producao_com_storage() -> Any:
    """Produção com storage e antivírus configurados — o arranjo do incidente."""
    from appsell_api.config import Settings

    return Settings(
        environment="production",
        database_url="postgresql+asyncpg://runtime@db.example.test:5432/appsell?ssl=require",
        **INTERNAL_AUTH,
        **REMOTE_STORAGE,
    )


@contextmanager
def _ready_respondendo(pronto: bool):  # type: ignore[no-untyped-def]
    """Fixa a resposta das dependências sem precisar de banco, storage ou sandbox."""
    original = main._dependencias_prontas
    main._dependencias_prontas = _sempre(
        {
            "pronto": pronto,
            "database": pronto,
            "storage": pronto,
            "pdf_sandbox": pronto,
        }
    )
    try:
        yield
    finally:
        main._dependencias_prontas = original
