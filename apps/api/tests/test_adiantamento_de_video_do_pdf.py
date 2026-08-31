"""O adiantamento dos vídeos do PDF, e o que ele não pode atrapalhar.

Abrir a aula começa a preparar os vídeos que o PDF cita, para que o aluno não
espere quando chegar na página do botão. O ganho é invisível — o que aparece na
tela é o mesmo — e é por isso que estes testes existem.

O que eles guardam é sobretudo o **limite**. O adiantamento acontece sem
ninguém pedir, e num arquivo que quem escreveu foi o vendedor: não pode tomar a
fila de quem clicou, não pode baixar o mesmo arquivo várias vezes, e não pode
gastar o orçamento com o que já está pronto.
"""

from __future__ import annotations

import asyncio
import threading
import time
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from anyio import to_thread

from appsell_api import learning_routes, pdf_link_video
from appsell_api.pdf_link_video import (
    MAX_PENDING_PREPARATIONS,
    PreparationRegistry,
    derivative_key,
)
from appsell_api.storage_s3 import S3Storage

RAIZ = "https://tcplgbtdodjlvforijdd.supabase.co/storage/v1/object/public/aulas"
ORIGEM = f"{RAIZ}/um.mov"
OUTRA = f"{RAIZ}/dois.mov"
VENDEDOR = "9f1c2f3e-4d5a-4b6c-8d7e-0a1b2c3d4e5f"


ASSET = "3a2b1c0d-9e8f-4a7b-8c6d-5e4f3a2b1c0d"


def _manifest(asset: str = ASSET) -> dict[str, Any]:
    return {"seller_organization_id": VENDEDOR, "asset_id": asset}


def _request(*, storage: object | None = object()) -> Any:
    settings = SimpleNamespace(storage_configured=True, storage_ready_bucket="appsell-ready")
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(storage=storage, settings=settings))
    )


@pytest.fixture(autouse=True)
def _fila_limpa(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Cada teste começa com a fila, o registro e a allowlist conhecidos."""
    monkeypatch.setenv(
        "APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", "https://tcplgbtdodjlvforijdd.supabase.co"
    )
    learning_routes._PDF_LINK_VIDEO_TASKS.clear()
    learning_routes._WARM_TASKS.clear()
    learning_routes._WARM_IN_FLIGHT.clear()
    monkeypatch.setattr(learning_routes, "_PDF_LINK_VIDEO_PREPARATIONS", PreparationRegistry())
    # Os semáforos são globais do módulo. Um teste que segura um slot e é
    # interrompido no meio deixaria o próximo esperando para sempre — e o
    # sintoma seria um teste que passa sozinho e falha dentro da suíte.
    # Recriados a partir das constantes do módulo, e não de um número escrito
    # aqui: um valor próprio esconderia justamente a mudança que estes testes
    # existem para vigiar.
    monkeypatch.setattr(
        learning_routes,
        "_PDF_LINK_VIDEO_SLOTS",
        asyncio.Semaphore(learning_routes.PDF_LINK_VIDEO_CONCURRENCY),
    )
    monkeypatch.setattr(
        learning_routes,
        "_PDF_LINK_VIDEO_TRANSCODE_SLOT",
        asyncio.Semaphore(learning_routes.PDF_LINK_VIDEO_TRANSCODE_CONCURRENCY),
    )
    monkeypatch.setattr(learning_routes, "_WARM_SLOTS", asyncio.Semaphore(1))
    yield
    for tarefa in [*learning_routes._PDF_LINK_VIDEO_TASKS, *learning_routes._WARM_TASKS]:
        tarefa.cancel()
    learning_routes._PDF_LINK_VIDEO_TASKS.clear()
    learning_routes._WARM_TASKS.clear()
    learning_routes._WARM_IN_FLIGHT.clear()


@pytest.fixture
def guardados() -> set[str]:
    """As chaves que o storage já tem."""
    return set()


@pytest.fixture
def preparos(monkeypatch: pytest.MonkeyPatch, guardados: set[str]) -> list[dict[str, Any]]:
    """Registra o que foi mandado preparar, sem baixar nem converter nada."""
    chamadas: list[dict[str, Any]] = []

    def _no_storage(_storage: Any, _bucket: str, key: str) -> Any:
        return {"key": key} if key in guardados else None

    async def _falso(_storage: Any, _bucket: str, key: str, target: str, *, replace: bool) -> None:
        chamadas.append({"key": key, "target": target, "replace": replace})
        await asyncio.sleep(0)

    monkeypatch.setattr(learning_routes, "_pdf_link_video_ready", _no_storage)
    monkeypatch.setattr(learning_routes, "_run_pdf_link_video_preparation", _falso)
    return chamadas


async def _rodar(request: Any, links: list[dict[str, Any]], asset: str = ASSET) -> None:
    learning_routes._warm_pdf_link_videos(request, _manifest(asset), links)
    await asyncio.gather(*list(learning_routes._WARM_TASKS))
    await asyncio.gather(*list(learning_routes._PDF_LINK_VIDEO_TASKS))


def _videos(*urls: str) -> list[dict[str, Any]]:
    return [{"kind": "video", "target_url": url} for url in urls]


async def test_adianta_os_videos_que_o_pdf_cita(preparos: list[dict[str, Any]]) -> None:
    await _rodar(_request(), _videos(ORIGEM, OUTRA))
    assert [chamada["target"] for chamada in preparos] == [ORIGEM, OUTRA]
    # Nunca substitui: decidir que o derivado guardado está velho é do clique,
    # que consultou a origem. O adiantamento só preenche o que falta.
    assert all(chamada["replace"] is False for chamada in preparos)


async def test_ignora_o_que_nao_e_video(preparos: list[dict[str, Any]]) -> None:
    await _rodar(
        _request(),
        [
            {"kind": "page", "target_url": None},
            {"kind": "external", "target_url": "https://exemplo.test/pagina"},
        ],
    )
    assert preparos == []


async def test_ignora_endereco_fora_da_origem_permitida(
    preparos: list[dict[str, Any]],
) -> None:
    # A mesma fronteira do clique. O adiantamento é um caminho novo até o
    # download, e um PDF de terceiro cita o endereço que quiser.
    await _rodar(_request(), _videos("https://outro.example.test/v.mp4"))
    assert preparos == []


async def test_nao_faz_nada_sem_storage(preparos: list[dict[str, Any]]) -> None:
    await _rodar(_request(storage=None), _videos(ORIGEM))
    assert preparos == []


async def test_o_mesmo_arquivo_com_ancoras_diferentes_e_um_download_so(
    preparos: list[dict[str, Any]],
) -> None:
    """Quatro botões para o mesmo vídeo, um preparo.

    O `#` de uma URL nunca chega ao servidor HTTP — é do navegador. Mas entrava
    na chave do derivado, e a chave é o registro do que já foi preparado. Um PDF
    citando `um.mov#1` até `um.mov#4` fazia a plataforma baixar quatro vezes o
    mesmo arquivo e guardar quatro cópias idênticas com nomes diferentes.

    Isso valia para o clique também. Passou a doer aqui porque agora o download
    começa sozinho, na abertura da aula, sem ninguém clicar em nada.
    """
    await _rodar(_request(), _videos(f"{ORIGEM}#1", f"{ORIGEM}#2", f"{ORIGEM}#3", ORIGEM))
    assert len(preparos) == 1
    assert preparos[0]["target"] == ORIGEM


async def test_nao_repete_o_mesmo_endereco(preparos: list[dict[str, Any]]) -> None:
    await _rodar(_request(), _videos(ORIGEM, ORIGEM))
    assert len(preparos) == 1


async def test_respeita_a_recusa_recente(preparos: list[dict[str, Any]]) -> None:
    learning_routes._PDF_LINK_VIDEO_PREPARATIONS.reject(derivative_key(VENDEDOR, ORIGEM))
    await _rodar(_request(), _videos(ORIGEM))
    assert preparos == []


async def test_nao_gasta_o_orcamento_com_quem_ja_esta_pronto(
    preparos: list[dict[str, Any]], guardados: set[str]
) -> None:
    """O que já está no storage não conta contra o orçamento.

    Sem esta consulta, a segunda abertura da mesma aula recriava tarefas para os
    vídeos já prontos — que só descobriam isso lá dentro, depois de gastar o
    orçamento. O vídeo que ainda faltava nunca chegava a ser agendado: o ganho
    parava nos primeiros e não avançava nunca.
    """
    orcamento = learning_routes._WARM_PREPARATION_BUDGET
    origens = [f"{RAIZ}/v{indice}.mov" for indice in range(orcamento + 1)]
    # Todos prontos, menos o último.
    guardados.update(derivative_key(VENDEDOR, origem) for origem in origens[:-1])

    await _rodar(_request(), _videos(*origens))

    assert [chamada["target"] for chamada in preparos] == [origens[-1]]


async def test_deixa_metade_da_fila_para_quem_clicou(
    preparos: list[dict[str, Any]],
) -> None:
    """O adiantamento para na metade da fila, e não no teto.

    Sem esta reserva, um PDF com oito vídeos encheria a fila inteira e o próximo
    aluno que clicasse num botão receberia o prazo longo de fila cheia — mais
    lento do que antes de existir adiantamento.
    """
    orcamento = learning_routes._WARM_PREPARATION_BUDGET
    origens = [f"{RAIZ}/v{indice}.mov" for indice in range(MAX_PENDING_PREPARATIONS + 2)]

    await _rodar(_request(), _videos(*origens))

    assert len(preparos) == orcamento
    assert orcamento < MAX_PENDING_PREPARATIONS


async def test_dois_preparos_vivos_e_uma_conversao(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dois preparos correm juntos; a conversão de cada um espera a vez.

    Os dois limites resolvem coisas diferentes. O download é da API e domina o
    tempo — 1,3 s contra 0,6 s no vídeo do vendedor —, então serializá-lo fazia
    o segundo aluno esperar sem motivo.

    A conversão é o contrário: o serviço isolado atende um por vez e **recusa**
    quem chega junto, com `429`. Quem leva a recusa insiste quatro vezes, de três
    em três segundos, e desiste de vez. Duas conversões chegando juntas não
    formariam fila — a segunda teria nove segundos para a primeira terminar, e um
    vídeo grande passa disso. O aluno ficaria sem vídeo nenhum.
    """
    convertendo = asyncio.Semaphore(0)
    solta = asyncio.Event()
    vivos = 0
    maximo_vivo = 0

    monkeypatch.setattr(learning_routes, "_pdf_link_video_ready", lambda *_: None)

    async def _preso(_storage: Any, _bucket: str, _key: str, _target: str) -> None:
        nonlocal vivos, maximo_vivo
        vivos += 1
        maximo_vivo = max(maximo_vivo, vivos)
        try:
            async with learning_routes._PDF_LINK_VIDEO_TRANSCODE_SLOT:
                convertendo.release()
                await solta.wait()
        finally:
            vivos -= 1

    monkeypatch.setattr(learning_routes, "_store_pdf_link_video", _preso)

    tarefas = [
        asyncio.create_task(
            learning_routes._run_pdf_link_video_preparation(
                cast(S3Storage, object()),
                "appsell-ready",
                f"chave-{indice}",
                ORIGEM,
                replace=False,
            )
        )
        for indice in range(4)
    ]
    try:
        # Uma conversão entra. A segunda fica na porta mesmo com o preparo vivo.
        await asyncio.wait_for(convertendo.acquire(), timeout=2)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(convertendo.acquire(), timeout=0.2)
        assert maximo_vivo == 2
    finally:
        solta.set()
        await asyncio.gather(*tarefas, return_exceptions=True)


async def test_duas_conversoes_nunca_se_sobrepoem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dois preparos de verdade, e as conversões em fila.

    O caminho é o real de ponta a ponta: `_store_pdf_link_video` chama
    `prepare_video`, que espera o portão antes de falar com o serviço isolado.
    Só o download e o próprio serviço são dublados.

    Um teste que segurasse o semáforo por fora provaria que semáforos funcionam
    e nada sobre este código usar o dele. Este falha se alguém tirar o
    `transcode_gate` da chamada — e o que acontece em produção nesse caso é o
    serviço isolado responder `429` ao segundo, que insiste quatro vezes, de três
    em três segundos, e desiste: o aluno fica sem vídeo.
    """
    # Contador com trava, não semáforo do asyncio: quem conta é a thread da
    # conversão, e as primitivas do laço não são seguras fora dele.
    tranca = threading.Lock()
    convertendo = 0
    maximo_convertendo = 0
    solta = threading.Event()

    async def _download(_target: str, destino: Path) -> tuple[int, str]:
        await to_thread.run_sync(partial(destino.write_bytes, b"origem"))
        return 6, "validador"

    def _remux(_source: Path, _size: int, _sha: str, destination: Path) -> bool:
        nonlocal convertendo, maximo_convertendo
        with tranca:
            convertendo += 1
            maximo_convertendo = max(maximo_convertendo, convertendo)
        try:
            # Segura enquanto o outro tenta entrar. Sem o portão, os dois
            # estariam aqui juntos — que é o que o serviço isolado recusa.
            solta.wait(timeout=10)
        finally:
            with tranca:
                convertendo -= 1
        destination.write_bytes(b"derivado")
        return True

    async def _ate(condicao: Any, prazo: float) -> bool:
        limite = time.monotonic() + prazo
        while time.monotonic() < limite:
            if condicao():
                return True
            await asyncio.sleep(0.01)
        return False

    monkeypatch.setattr(pdf_link_video, "download_source", _download)
    monkeypatch.setattr(pdf_link_video, "remux_in_sandbox", _remux)

    guardado: list[str] = []
    storage = cast(
        S3Storage,
        SimpleNamespace(put_verified=lambda **kwargs: guardado.append(kwargs["destination_key"])),
    )

    tarefas = [
        asyncio.create_task(
            learning_routes._store_pdf_link_video(storage, "appsell-ready", f"chave-{i}", ORIGEM)
        )
        for i in range(2)
    ]
    try:
        # A primeira conversão entra...
        assert await _ate(lambda: maximo_convertendo >= 1, prazo=5)
        # ...e a segunda não entra atrás dela. Aqui o tempo joga a favor: quanto
        # mais se espera, mais chance a segunda teria de entrar se pudesse.
        assert not await _ate(lambda: maximo_convertendo >= 2, prazo=0.5)
    finally:
        solta.set()
        erros = await asyncio.gather(*tarefas, return_exceptions=True)
    assert [erro for erro in erros if isinstance(erro, BaseException)] == []
    assert sorted(guardado) == ["chave-0", "chave-1"]
    assert maximo_convertendo == 1


async def test_varias_aberturas_juntas_nao_estouram_a_fila(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Três PDFs abertos ao mesmo tempo não viram doze preparos.

    Contar só o que cada abertura criou não bastava: três chamadas de quatro
    passavam de doze numa fila cujo teto é oito, e o clique seguinte caía no
    prazo longo de fila cheia. O limite tem de valer entre as aberturas, e não
    dentro de cada uma.
    """
    vivos = 0
    maximo_vivo = 0
    solta = asyncio.Event()

    monkeypatch.setattr(learning_routes, "_pdf_link_video_ready", lambda *_: None)

    async def _preso(
        _storage: Any, _bucket: str, _key: str, _target: str, *, replace: bool
    ) -> None:
        nonlocal vivos, maximo_vivo
        vivos += 1
        maximo_vivo = max(maximo_vivo, vivos)
        try:
            await solta.wait()
        finally:
            vivos -= 1

    monkeypatch.setattr(learning_routes, "_run_pdf_link_video_preparation", _preso)

    for indice in range(3):
        learning_routes._warm_pdf_link_videos(
            _request(),
            _manifest(f"asset-{indice}"),
            _videos(*[f"{RAIZ}/a{indice}-{n}.mov" for n in range(4)]),
        )
    try:
        await asyncio.sleep(0.05)
        # Um preparo adiantado por vez, em toda a máquina. O outro slot fica
        # livre para quem clicou.
        assert maximo_vivo == 1
        assert len(learning_routes._PDF_LINK_VIDEO_TASKS) == 1
    finally:
        solta.set()
        await asyncio.gather(*list(learning_routes._WARM_TASKS), return_exceptions=True)


async def test_o_mesmo_pdf_aberto_por_varios_alunos_e_um_adiantamento_so(
    preparos: list[dict[str, Any]],
) -> None:
    # Sem isto, cada aluno que abrisse a mesma aula repetiria a varredura
    # inteira do storage para descobrir a mesma coisa.
    for _ in range(3):
        learning_routes._warm_pdf_link_videos(_request(), _manifest(), _videos(ORIGEM))
    assert len(learning_routes._WARM_TASKS) == 1
    await asyncio.gather(*list(learning_routes._WARM_TASKS))
    assert len(preparos) == 1


def test_a_rota_do_pdf_adianta_os_videos() -> None:
    """`inspect_asset_pages` chama o adiantamento.

    Os outros testes chamam `_warm_pdf_link_videos` direto e passariam com a
    chamada removida da rota — o adiantamento simplesmente não aconteceria, e
    nada acusaria. Esta é a linha que liga as duas coisas.
    """
    fonte = Path(learning_routes.__file__).read_text()
    corpo = fonte[fonte.index("async def inspect_asset_pages(") :]
    corpo = corpo[: corpo.index("\ndef _warm_pdf_link_videos(")]
    assert "_warm_pdf_link_videos(request, manifest, links)" in corpo
