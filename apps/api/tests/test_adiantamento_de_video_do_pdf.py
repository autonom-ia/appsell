"""O adiantamento dos vídeos do PDF, e o que ele não pode atrapalhar.

Abrir o PDF começa a preparar os vídeos que ele cita, para que o aluno não
espere quando chegar na página do botão. O ganho é invisível — o que aparece na
tela é o mesmo — e é por isso que estes testes existem.

O que eles guardam é sobretudo o **limite**: o adiantamento não pode tomar a
fila de quem clicou. Quem clicou está esperando na frente da tela; quem foi
adiantado ainda nem chegou na página.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from appsell_api import learning_routes
from appsell_api.pdf_link_video import (
    MAX_PENDING_PREPARATIONS,
    PreparationRegistry,
    derivative_key,
)
from appsell_api.storage_s3 import S3Storage

ORIGEM = "https://tcplgbtdodjlvforijdd.supabase.co/storage/v1/object/public/aulas/um.mov"
OUTRA = "https://tcplgbtdodjlvforijdd.supabase.co/storage/v1/object/public/aulas/dois.mov"
VENDEDOR = "9f1c2f3e-4d5a-4b6c-8d7e-0a1b2c3d4e5f"


def _manifest() -> dict[str, Any]:
    return {"seller_organization_id": VENDEDOR}


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
    monkeypatch.setattr(learning_routes, "_PDF_LINK_VIDEO_PREPARATIONS", PreparationRegistry())
    yield
    for tarefa in list(learning_routes._PDF_LINK_VIDEO_TASKS):
        tarefa.cancel()
    learning_routes._PDF_LINK_VIDEO_TASKS.clear()


@pytest.fixture
def preparos(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Registra o que foi mandado preparar, sem baixar nem converter nada."""
    chamadas: list[dict[str, Any]] = []

    async def _falso(storage: Any, bucket: str, key: str, target: str, *, replace: bool) -> None:
        chamadas.append({"key": key, "target": target, "replace": replace})
        await asyncio.sleep(0)

    monkeypatch.setattr(learning_routes, "_run_pdf_link_video_preparation", _falso)
    return chamadas


async def _rodar(request: Any, links: list[dict[str, Any]]) -> None:
    learning_routes._warm_pdf_link_videos(request, _manifest(), links)
    await asyncio.gather(*list(learning_routes._PDF_LINK_VIDEO_TASKS))


async def test_adianta_os_videos_que_o_pdf_cita(preparos: list[dict[str, Any]]) -> None:
    await _rodar(
        _request(),
        [
            {"kind": "video", "target_url": ORIGEM},
            {"kind": "video", "target_url": OUTRA},
        ],
    )
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
    # A mesma fronteira do clique. Um PDF de terceiro cita o que quiser, e o
    # adiantamento é um caminho novo até o download — teria de ser conferido de
    # novo, e é.
    await _rodar(_request(), [{"kind": "video", "target_url": "https://outro.example.test/v.mp4"}])
    assert preparos == []


async def test_nao_faz_nada_sem_storage(preparos: list[dict[str, Any]]) -> None:
    await _rodar(_request(storage=None), [{"kind": "video", "target_url": ORIGEM}])
    assert preparos == []


async def test_nao_repete_o_mesmo_endereco(preparos: list[dict[str, Any]]) -> None:
    # Dois botões apontando para o mesmo vídeo são um preparo só, não dois.
    await _rodar(
        _request(),
        [
            {"kind": "video", "target_url": ORIGEM},
            {"kind": "video", "target_url": ORIGEM},
        ],
    )
    assert len(preparos) == 1


async def test_respeita_a_recusa_recente(preparos: list[dict[str, Any]]) -> None:
    chave = derivative_key(VENDEDOR, ORIGEM)
    learning_routes._PDF_LINK_VIDEO_PREPARATIONS.reject(chave)
    await _rodar(_request(), [{"kind": "video", "target_url": ORIGEM}])
    assert preparos == []


async def test_deixa_metade_da_fila_para_quem_clicou(
    preparos: list[dict[str, Any]],
) -> None:
    """O adiantamento para na metade, e não no teto.

    Sem esta reserva, um PDF com oito vídeos encheria a fila inteira e o próximo
    aluno que clicasse num botão receberia o prazo longo de fila cheia — mais
    lento do que antes de existir adiantamento.
    """
    orcamento = MAX_PENDING_PREPARATIONS // 2
    origens = [f"{ORIGEM}?n={indice}" for indice in range(MAX_PENDING_PREPARATIONS + 2)]
    links = [{"kind": "video", "target_url": origem} for origem in origens]

    ocupadas: list[asyncio.Task[None]] = []

    async def _presa() -> None:
        await asyncio.Event().wait()

    def _segurar() -> None:
        tarefa: asyncio.Task[None] = asyncio.create_task(_presa())
        ocupadas.append(tarefa)
        learning_routes._PDF_LINK_VIDEO_TASKS.add(tarefa)

    try:
        learning_routes._warm_pdf_link_videos(_request(), _manifest(), links)
        assert len(learning_routes._PDF_LINK_VIDEO_TASKS) == orcamento
        assert orcamento < MAX_PENDING_PREPARATIONS

        # E com a fila já na metade por outro motivo, o adiantamento não começa
        # nada: a reserva vale contra a fila inteira, não contra a sua parte.
        learning_routes._PDF_LINK_VIDEO_TASKS.clear()
        for _ in range(orcamento):
            _segurar()
        learning_routes._warm_pdf_link_videos(_request(), _manifest(), links)
        assert len(learning_routes._PDF_LINK_VIDEO_TASKS) == orcamento
    finally:
        for tarefa in ocupadas:
            tarefa.cancel()


async def test_tres_preparos_correm_juntos(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fila de preparo não é de um em um.

    Medido no vídeo do vendedor: 1,3 s de download contra 0,6 s de conversão. O
    que domina é a rede, e ela é da API — a conversão acontece no serviço
    isolado, que atende um por vez de propósito. Serializar tudo aqui fazia o
    quarto aluno esperar a soma dos três downgrades anteriores sem motivo.

    Este teste falha se alguém voltar o semáforo para um: são os segundos que o
    aluno espera olhando para "preparando".
    """
    dentro = asyncio.Semaphore(0)
    solta = asyncio.Event()

    monkeypatch.setattr(learning_routes, "_pdf_link_video_ready", lambda *_: None)

    async def _preso(*_args: Any, **_kwargs: Any) -> None:
        dentro.release()
        await solta.wait()

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
        # Três entram. O quarto fica na porta: `acquire` com prazo é o que
        # distingue "ainda não chegou" de "não cabe".
        for _ in range(3):
            await asyncio.wait_for(dentro.acquire(), timeout=2)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(dentro.acquire(), timeout=0.2)
    finally:
        solta.set()
        await asyncio.gather(*tarefas, return_exceptions=True)
