"""#242 — o player não entrega os materiais de aulas que o aluno não abriu."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import psycopg
import pytest
from psycopg.types.json import Jsonb

from appsell_api.reset_demo_tenant import load_manifest, reset_demo

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.environ.get("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY_HEX = os.environ.get("APPSELL_TEST_CONTEXT_KEY_HEX")
ISSUER = "https://identity.demo.test.autonomia.site"

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY_HEX)),
    reason="isolated PostgreSQL environment is required",
)

MATERIAL: dict[str, Any] = {
    "id": "9f000000-0000-4000-8000-000000000001",
    "kind": "external",
    "title": "Planilha de apoio",
    "url": "https://exemplo.test/planilha-secreta",
    "provider": None,
    "video_id": None,
    "position": 0,
}


def identity_id(subject: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"{ISSUER}\0{subject}")


def apply_context(connection: psycopg.Connection[Any], actor: UUID, capability: str) -> None:
    issued = int(time.time())
    expires = issued + 60
    payload = f"{actor}::{capability}:{issued}:{expires}"
    signature = hmac.new(
        bytes.fromhex(CONTEXT_KEY_HEX or ""), payload.encode(), hashlib.sha256
    ).hexdigest()
    connection.execute(
        "select app_private.apply_request_context(%s,null,%s,%s,%s,%s)",
        (actor, capability, issued, expires, signature),
    )


def with_materials_on_every_lesson(snapshot: dict[str, Any]) -> dict[str, Any]:
    for course in snapshot.get("courses", []):
        for module in course.get("modules", []):
            for lesson in module.get("lessons", []):
                lesson["materials"] = [MATERIAL]
    return snapshot


@pytest.fixture(autouse=True)
def demo_with_published_materials() -> dict[str, Any]:
    """Uma publicação como as de depois da 0036: com materiais no snapshot.

    A demonstração é anterior à 0036 e por isso não carrega `materials`. O
    caminho normal para produzir uma que carregue é publicar um produto inteiro,
    e o gatilho `publication_immutable` impede corrigir a publicação depois.
    Desligar esse gatilho aqui, como administrador e dentro da fixture, é o que
    permite montar o estado que o defeito exige sem reescrever o fluxo de
    publicação dentro de um teste.
    """
    assert ADMIN_URL
    manifest, manifest_sha = load_manifest()
    with psycopg.connect(ADMIN_URL) as connection:
        reset_demo(connection, manifest, manifest_sha, ISSUER)
        connection.execute(
            """
            insert into buyer_entitlement(
              seller_organization_id,id,buyer_identity_user_id,catalog_product_id,
              publication_id,reference_key,source,status,progress_percent
            ) values(%s,%s,%s,%s,%s,'pytest-materials-fixture','demo_seed','active',0)
            """,
            (
                UUID(manifest["seller"]["id"]),
                UUID(manifest["entitlement"]["id"]),
                identity_id(manifest["personas"]["buyer"]["subject"]),
                UUID(manifest["product"]["id"]),
                UUID(manifest["publication"]["id"]),
            ),
        )
        row = connection.execute(
            "select snapshot from publication where id=%s",
            (UUID(manifest["publication"]["id"]),),
        ).fetchone()
        assert row is not None
        connection.execute("alter table publication disable trigger publication_immutable")
        connection.execute(
            "update publication set snapshot=%s where id=%s",
            (Jsonb(with_materials_on_every_lesson(row[0])), UUID(manifest["publication"]["id"])),
        )
        connection.execute("alter table publication enable trigger publication_immutable")
        connection.commit()
    return dict(manifest)


def read_player(manifest: dict[str, Any], lesson: UUID | None) -> dict[str, Any]:
    assert RUNTIME_URL
    buyer = identity_id(manifest["personas"]["buyer"]["subject"])
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, buyer, "learning.player.read")
        row = connection.execute(
            "select app_private.read_learning_player(%s,%s,%s)",
            (buyer, UUID(manifest["entitlement"]["id"]), lesson),
        ).fetchone()
        connection.rollback()
    assert row is not None
    return dict(row[0])


def lessons_of(player: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        lesson
        for course in [player.get("course") or {}]
        for module in course.get("modules", [])
        for lesson in module.get("lessons", [])
    ]


def test_only_the_open_lesson_carries_its_material_links(
    demo_with_published_materials: dict[str, Any],
) -> None:
    manifest = demo_with_published_materials
    player = read_player(manifest, None)
    selected = UUID(str(player["selected_lesson_id"]))
    aulas = lessons_of(player)

    assert len(aulas) > 1, "o cenário precisa de mais de uma aula para dizer alguma coisa"
    abertas = [lesson for lesson in aulas if UUID(str(lesson["id"])) == selected]
    fechadas = [lesson for lesson in aulas if UUID(str(lesson["id"])) != selected]
    assert abertas and fechadas

    # A aula aberta entrega o material: é o conteúdo dela.
    assert abertas[0]["materials"] == [MATERIAL]
    # As outras não. O endereço do material é o conteúdo de um material externo,
    # e esconder `content` sem esconder a URL não escondia nada.
    for lesson in fechadas:
        assert lesson["materials"] == []
        assert lesson["content"] == ""


def test_no_unopened_lesson_leaks_the_address_anywhere_in_the_payload(
    demo_with_published_materials: dict[str, Any],
) -> None:
    # A asserção por aula pode passar enquanto a URL sobrevive noutro ramo da
    # resposta. Esta varre a carga inteira: o endereço só pode aparecer o número
    # de vezes que a aula aberta o traz.
    manifest = demo_with_published_materials
    player = read_player(manifest, None)
    aulas = lessons_of(player)
    selected = UUID(str(player["selected_lesson_id"]))
    esperado = sum(1 for lesson in aulas if UUID(str(lesson["id"])) == selected)

    assert str(player).count(str(MATERIAL["url"])) == esperado


def test_choosing_another_lesson_moves_which_material_is_delivered(
    demo_with_published_materials: dict[str, Any],
) -> None:
    # Prova que o esconderijo segue a seleção, e não uma posição fixa.
    manifest = demo_with_published_materials
    primeiro = read_player(manifest, None)
    outra = next(
        UUID(str(lesson["id"]))
        for lesson in lessons_of(primeiro)
        if UUID(str(lesson["id"])) != UUID(str(primeiro["selected_lesson_id"]))
    )

    player = read_player(manifest, outra)

    for lesson in lessons_of(player):
        esperado = [MATERIAL] if UUID(str(lesson["id"])) == outra else []
        assert lesson["materials"] == esperado
