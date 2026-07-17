#!/usr/bin/env python3
"""Generate deterministic, protected demo PDFs for the Codex course."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import zlib
from pathlib import Path
from uuid import UUID, uuid5

from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from appsell_api.pdf_derivatives import (
    MAX_PDF_PAGES,
    PdfDerivativeRejected,
    _canonical_png_bytes,
    build_pdf_page_archive,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "pdf" / "codex"
MANIFEST = ROOT / "lib" / "demo-codex-assets.json"
SEED_MANIFEST = ROOT / "demo" / "content" / "codex-na-pratica" / "manifest.json"
DEMO_NAMESPACE = UUID("97c8c89a-f0d2-4d1c-8cbb-5f0e9e5de330")
DEMO_COUPON_ID = "eb36c13c-93a1-5bcc-a50f-310572690d5f"


def stable_id(name: str) -> str:
    return str(uuid5(DEMO_NAMESPACE, name))


INK = colors.HexColor("#111827")
BLUE = colors.HexColor("#3156D3")
CYAN = colors.HexColor("#21B8C7")
PALE = colors.HexColor("#EEF3FF")
MUTED = colors.HexColor("#5D6678")
WHITE = colors.white

LESSONS = [
    {
        "slug": "01-o-que-e-codex",
        "title": "O que é Codex e quando usar",
        "summary": "Entenda o papel do Codex como parceiro de engenharia e operador de software.",
        "outcomes": [
            "Diferenciar conversa, diagnóstico, implementação e operação.",
            "Escolher tarefas adequadas para automação com supervisão humana.",
            "Reconhecer ações que exigem aprovação explícita.",
        ],
        "sections": [
            (
                "Um agente, não um botão mágico",
                "Codex trabalha melhor quando recebe um objetivo verificável, acesso ao contexto correto e limites claros. Ele pode ler código, executar testes, editar arquivos e organizar um fluxo de entrega. A qualidade da saída depende da qualidade da evidência disponível.",
            ),
            (
                "Quando usar",
                "Use em investigação de bugs, criação de funcionalidades, refactors, documentação, testes e preparação de pull requests. Para produção, billing, secrets, dados de clientes e operações irreversíveis, mantenha aprovação humana e plano de rollback.",
            ),
            (
                "Exercício",
                "Escolha uma tarefa real e escreva: resultado esperado, arquivos ou sistemas em escopo, restrições, evidências mínimas e ações proibidas.",
            ),
        ],
    },
    {
        "slug": "02-contexto-repo-objetivo",
        "title": "Como preparar contexto, repo e objetivo",
        "summary": "Dê ao agente informação suficiente para agir sem inventar fatos.",
        "outcomes": [
            "Escrever um objetivo mensurável.",
            "Delimitar repositório, ambiente e dados autorizados.",
            "Definir o que significa pronto antes de começar.",
        ],
        "sections": [
            (
                "O briefing mínimo",
                "Inclua problema, usuário afetado, resultado esperado, ambiente, restrições e critérios de aceite. Aponte documentos e caminhos reais. Se houver decisões anteriores, registre-as no repositório.",
            ),
            (
                "Contexto seguro",
                "Nunca cole secrets em prompts ou commits. Prefira variáveis de ambiente e cofres. Diga explicitamente se produção está fora do escopo. Em worktrees sujas, preserve mudanças existentes e faça commits cirúrgicos.",
            ),
            (
                "Modelo de pedido",
                "Implemente [resultado] em [repo]. Preserve [contratos]. Não altere [fora de escopo]. Valide com [testes]. Abra PR, mas não faça merge ou deploy.",
            ),
        ],
    },
    {
        "slug": "03-implementar-com-seguranca",
        "title": "Como pedir implementação com segurança",
        "summary": "Transforme intenção em mudanças pequenas, revisáveis e reversíveis.",
        "outcomes": [
            "Separar slices por risco.",
            "Aplicar defesa em profundidade para auth e multi-tenant.",
            "Exigir testes proporcionais ao impacto.",
        ],
        "sections": [
            (
                "Slices e blast radius",
                "Prefira uma mudança coesa por branch. Auth, billing, migrations e deploy devem ter gates próprios. Explique quem pode chamar cada operação, quais dados mudam e como reverter.",
            ),
            (
                "Multi-tenant de verdade",
                "Não confie apenas em filtros da interface. Vincule chaves estrangeiras ao tenant, aplique autorização no servidor e use RLS quando disponível. Teste acesso cruzado com identidades e organizações diferentes.",
            ),
            (
                "Idempotência e concorrência",
                "Mutações repetíveis precisam de chave idempotente vinculada ao ator, recurso e corpo. Use versões esperadas ou locks para impedir sobrescrita silenciosa. Teste retries e corridas reais.",
            ),
        ],
    },
    {
        "slug": "04-revisar-testes-pr",
        "title": "Como revisar testes, PR e evidências",
        "summary": "Avalie o que foi provado, e não apenas o que foi declarado.",
        "outcomes": [
            "Ler diffs com mentalidade adversarial.",
            "Separar testes unitários, integração e E2E.",
            "Reconhecer um verde falso.",
        ],
        "sections": [
            (
                "Review adversarial",
                "Tente quebrar o fluxo: payload máximo, retry, concorrência, sessão expirada, tenant errado, viewport pequeno e dependência indisponível. Achados P0 a P2 bloqueiam avanço.",
            ),
            (
                "Pirâmide de evidência",
                "Tipos e lint detectam classes de erro. Unitários verificam regras locais. Integração prova banco e contratos. E2E prova o caminho do usuário. CI remoto confirma que o resultado não depende da máquina do autor.",
            ),
            (
                "Checklist da PR",
                "Confirme objetivo, escopo, riscos, migrations, segurança, testes, plano de rollback, itens fora de escopo e ausência de secrets. Verifique o SHA exato revisado antes do merge.",
            ),
        ],
    },
    {
        "slug": "05-deploy-e-rollback",
        "title": "Como operar deploy e rollback",
        "summary": "Promova mudanças com gates claros e retorno seguro.",
        "outcomes": [
            "Distinguir merge, release e deploy.",
            "Planejar rollback antes da promoção.",
            "Coletar evidência operacional pós-deploy.",
        ],
        "sections": [
            (
                "Antes do deploy",
                "Exija aprovação, CI verde, backup quando aplicável, migrations compatíveis e observabilidade pronta. Confirme domínio, TLS, healthchecks, variáveis e dependências externas.",
            ),
            (
                "Durante e depois",
                "Promova um artefato imutável. Observe erros, latência, filas, banco e fluxos críticos. Um status 200 isolado não prova side effects nem autorização correta.",
            ),
            (
                "Rollback honesto",
                "Defina gatilhos, responsável e passos. Migrations forward-only pedem rollback de aplicação compatível, não downgrade destrutivo. Registre o que aconteceu e transforme incidentes em testes.",
            ),
        ],
    },
]


def styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "eyebrow": ParagraphStyle(
            "Eyebrow",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
            textColor=BLUE,
            spaceAfter=5,
        ),
        "title": ParagraphStyle(
            "Title",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=25,
            leading=29,
            textColor=INK,
            alignment=TA_LEFT,
            spaceAfter=10,
        ),
        "lead": ParagraphStyle(
            "Lead",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=11,
            leading=16,
            textColor=MUTED,
            spaceAfter=14,
        ),
        "h2": ParagraphStyle(
            "H2",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=15,
            leading=19,
            textColor=INK,
            spaceBefore=10,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=10,
            leading=15,
            textColor=INK,
            spaceAfter=9,
        ),
        "small": ParagraphStyle(
            "Small",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=8,
            leading=11,
            textColor=MUTED,
        ),
    }


def page_chrome(canvas, document) -> None:  # type: ignore[no-untyped-def]
    canvas.saveState()
    width, height = A4
    # Paint an explicit opaque page background. Leaving the PDF page transparent
    # makes some native and Poppler renderers choose a black canvas.
    canvas.setFillColor(WHITE)
    canvas.rect(0, 0, width, height, stroke=0, fill=1)
    canvas.setFillColor(BLUE)
    canvas.rect(0, height - 8 * mm, width, 8 * mm, stroke=0, fill=1)
    canvas.setFillColor(INK)
    canvas.setFont("Helvetica-Bold", 8)
    canvas.drawString(18 * mm, 12 * mm, "AUTONOM.IA SELL  /  CODEX NA PRÁTICA")
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(width - 18 * mm, 12 * mm, f"Página {document.page}")
    canvas.restoreState()


def build_lesson(index: int, lesson: dict[str, object], target: Path) -> None:
    sheet = styles()
    document = SimpleDocTemplate(
        str(target),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title=str(lesson["title"]),
        author="Autonom.ia",
        creator="Autonom.ia Sell",
        invariant=1,
    )
    story = [
        Paragraph(f"AULA {index:02d}  ·  GUIA PRÁTICO", sheet["eyebrow"]),
        Paragraph(str(lesson["title"]), sheet["title"]),
        Paragraph(str(lesson["summary"]), sheet["lead"]),
        Table(
            [
                [
                    Paragraph("OBJETIVO", sheet["eyebrow"]),
                    Paragraph(
                        "Aplicar o conteúdo em uma tarefa real ao final da aula.",
                        sheet["body"],
                    ),
                ]
            ],
            colWidths=[34 * mm, 136 * mm],
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), PALE),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD7FF")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]
            ),
        ),
        Spacer(1, 6 * mm),
        Paragraph("Ao concluir, você será capaz de", sheet["h2"]),
        ListFlowable(
            [
                ListItem(Paragraph(str(item), sheet["body"]), leftIndent=6)
                for item in lesson["outcomes"]
            ],
            bulletType="bullet",
            bulletColor=CYAN,
            leftIndent=16,
        ),
        Spacer(1, 3 * mm),
    ]
    for heading, body in lesson["sections"]:
        story.append(
            KeepTogether(
                [
                    Paragraph(str(heading), sheet["h2"]),
                    Paragraph(str(body), sheet["body"]),
                ]
            )
        )
    story.extend(
        [
            Spacer(1, 6 * mm),
            Table(
                [
                    [
                        Paragraph("PRÓXIMO PASSO", sheet["eyebrow"]),
                        Paragraph(
                            "Registre a evidência do exercício e avance somente quando conseguir explicar o resultado.",
                            sheet["body"],
                        ),
                    ]
                ],
                colWidths=[34 * mm, 136 * mm],
                style=TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F2FCFD")),
                        ("BOX", (0, 0), (-1, -1), 0.5, CYAN),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 8),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                        ("TOPPADDING", (0, 0), (-1, -1), 8),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                    ]
                ),
            ),
        ]
    )
    document.build(story, onFirstPage=page_chrome, onLaterPages=page_chrome)


def _previous_lessons() -> dict[str, dict[str, object]]:
    if not MANIFEST.is_file():
        return {}
    try:
        parsed = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("committed demo raster manifest is invalid") from error
    if not isinstance(parsed, list):
        raise RuntimeError("committed demo raster manifest is invalid")
    lessons: dict[str, dict[str, object]] = {}
    for entry in parsed:
        if not isinstance(entry, dict) or not isinstance(entry.get("slug"), str):
            raise RuntimeError("committed demo raster manifest is invalid")
        slug = str(entry["slug"])
        if slug in lessons:
            raise RuntimeError(
                "committed demo raster manifest contains duplicate lessons"
            )
        lessons[slug] = entry
    return lessons


def _has_canonical_committed_png_structure(payload: bytes) -> bool:
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    offset = 8
    chunks: list[bytes] = []
    while offset < len(payload):
        if offset + 12 > len(payload):
            return False
        length = int.from_bytes(payload[offset : offset + 4], "big")
        chunk_type = payload[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        crc_end = data_end + 4
        if crc_end > len(payload):
            return False
        chunk_data = payload[data_start:data_end]
        expected_crc = int.from_bytes(payload[data_end:crc_end], "big")
        actual_crc = zlib.crc32(chunk_data, zlib.crc32(chunk_type)) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            return False
        if (chunk_type == b"IHDR" and length != 13) or (
            chunk_type == b"IEND" and length != 0
        ):
            return False
        chunks.append(chunk_type)
        offset = crc_end
        if chunk_type == b"IEND":
            break
    return (
        offset == len(payload)
        and len(chunks) >= 3
        and chunks[0] == b"IHDR"
        and chunks[-1] == b"IEND"
        and all(chunk == b"IDAT" for chunk in chunks[1:-1])
    )


def _committed_pages(
    *, slug: str, source_sha256: str, previous: dict[str, object] | None
) -> list[dict[str, object]]:
    if previous is None or previous.get("sha256") != source_sha256:
        raise RuntimeError(
            f"demo PDF {slug} changed; run this generator with --refresh-pages"
        )
    raster = previous.get("raster")
    prior_pages = raster.get("pages") if isinstance(raster, dict) else None
    if not isinstance(prior_pages, list) or not 1 <= len(prior_pages) <= MAX_PDF_PAGES:
        raise RuntimeError(f"committed demo raster pages for {slug} are invalid")

    root = OUTPUT / slug / "pages"
    expected_names = {f"{number}.png" for number in range(1, len(prior_pages) + 1)}
    try:
        entries = list(root.iterdir())
    except OSError as error:
        raise RuntimeError(
            f"committed demo raster pages for {slug} are unavailable"
        ) from error
    if {path.name for path in entries} != expected_names or any(
        not path.is_file() or path.is_symlink() for path in entries
    ):
        raise RuntimeError(f"committed demo raster pages for {slug} are incomplete")

    pages: list[dict[str, object]] = []
    for page_number in range(1, len(prior_pages) + 1):
        relative_key = f"{slug}/pages/{page_number}.png"
        path = OUTPUT / relative_key
        try:
            payload = path.read_bytes()
            _, width, height = _canonical_png_bytes(payload)
        except (OSError, PdfDerivativeRejected) as error:
            raise RuntimeError(
                f"committed demo raster page {relative_key} is invalid"
            ) from error
        if not _has_canonical_committed_png_structure(payload):
            raise RuntimeError(
                f"committed demo raster page {relative_key} is not canonical"
            )
        current = {
            "page_number": page_number,
            "storage_driver": "legacy_bundle",
            "storage_bucket": "codex-demo",
            "object_key": relative_key,
            "storage_version_id": None,
            "storage_etag": None,
            "byte_size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "pixel_width": width,
            "pixel_height": height,
        }
        if prior_pages[page_number - 1] != current:
            raise RuntimeError(
                f"committed demo raster page {relative_key} changed; "
                "run this generator with --refresh-pages"
            )
        pages.append(current)
    return pages


def _replace_directory(staged: Path, target: Path) -> None:
    if not staged.is_dir() or staged.is_symlink():
        raise RuntimeError("staged demo raster directory is invalid")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise RuntimeError("committed demo raster directory is invalid")
    if not target.exists():
        staged.rename(target)
        return

    backup = Path(tempfile.mkdtemp(prefix=f".{target.name}-backup-", dir=target.parent))
    backup.rmdir()
    target.rename(backup)
    try:
        staged.rename(target)
    except OSError:
        backup.rename(target)
        raise
    else:
        shutil.rmtree(backup)


def _atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
            destination.write(value)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(*, refresh_pages: bool = False) -> None:
    rl_config.invariant = 1
    OUTPUT.mkdir(parents=True, exist_ok=True)
    previous_lessons = _previous_lessons()
    with tempfile.TemporaryDirectory(prefix=".demo-codex-", dir=OUTPUT) as staging:
        staging_root = Path(staging)
        staged_pdfs: list[tuple[Path, Path]] = []
        staged_page_trees: list[tuple[Path, Path]] = []
        manifest = []
        for index, lesson in enumerate(LESSONS, start=1):
            filename = f"{lesson['slug']}.pdf"
            output = OUTPUT / filename
            staged_pdf = staging_root / filename
            build_lesson(index, lesson, staged_pdf)
            payload = staged_pdf.read_bytes()
            staged_pdfs.append((staged_pdf, output))
            asset_id = stable_id(f"asset:{lesson['slug']}")
            source_sha256 = hashlib.sha256(payload).hexdigest()
            slug = str(lesson["slug"])
            if refresh_pages:
                staged_lesson = staging_root / slug
                raster_root = staged_lesson / "pages"
                raster_root.mkdir(parents=True, mode=0o700)
                with tempfile.TemporaryDirectory(
                    prefix="appsell-demo-raster-"
                ) as temporary:
                    prepared = build_pdf_page_archive(
                        staged_pdf,
                        expected_size=len(payload),
                        expected_sha256_hex=source_sha256,
                        job_directory=Path(temporary),
                    )
                    pages = []
                    for page in prepared.pages:
                        relative_key = f"{slug}/pages/{page.page_number}.png"
                        destination = raster_root / f"{page.page_number}.png"
                        shutil.copyfile(page.path, destination)
                        pages.append(
                            {
                                "page_number": page.page_number,
                                "storage_driver": "legacy_bundle",
                                "storage_bucket": "codex-demo",
                                "object_key": relative_key,
                                "storage_version_id": None,
                                "storage_etag": None,
                                "byte_size": page.byte_size,
                                "sha256": page.sha256_hex,
                                "pixel_width": page.width,
                                "pixel_height": page.height,
                            }
                        )
                staged_page_trees.append((staged_lesson, OUTPUT / slug))
            else:
                pages = _committed_pages(
                    slug=slug,
                    source_sha256=source_sha256,
                    previous=previous_lessons.get(slug),
                )
            page_manifest = json.dumps(
                pages, sort_keys=True, separators=(",", ":")
            ).encode()
            manifest.append(
                {
                    "seller_id": stable_id("seller"),
                    "position": index - 1,
                    "slug": lesson["slug"],
                    "title": lesson["title"],
                    "summary": lesson["summary"],
                    "href": (
                        f"/api/library/items/{stable_id('entitlement:demo-user:codex-na-pratica')}"
                        f"/assets/{asset_id}"
                    ),
                    "bytes": len(payload),
                    "sha256": source_sha256,
                    "lesson_id": stable_id(f"lesson:{lesson['slug']}"),
                    "asset_id": asset_id,
                    "raster": {
                        "profile": "png-1600-v1",
                        "page_count": len(pages),
                        "total_bytes": sum(page["byte_size"] for page in pages),
                        "manifest_sha256": hashlib.sha256(page_manifest).hexdigest(),
                        "pages": pages,
                    },
                }
            )

        video_path = OUTPUT / "codex-em-3-passos.mp4"
        if not video_path.is_file() or video_path.is_symlink():
            raise RuntimeError("committed demo video is missing")
        video_payload = video_path.read_bytes()
        video = {
            "lesson_id": manifest[-1]["lesson_id"],
            "asset_id": stable_id("asset:codex-em-3-passos-video"),
            "slug": "codex-em-3-passos",
            "file_name": video_path.name,
            "title": "Codex em 3 passos",
            "bytes": len(video_payload),
            "sha256": hashlib.sha256(video_payload).hexdigest(),
            "position": 0,
        }
        seed = {
            "seed_key": "autonomia-demo-v1",
            "version": 2,
            "personas": {
                "admin": {
                    "subject": "demo-admin-v1",
                    "email": "admin.demo@autonomia.site",
                },
                "buyer": {
                    "subject": "demo-user-v1",
                    "email": "user.demo@autonomia.site",
                },
            },
            "seller": {
                "id": stable_id("seller"),
                "slug": "autonomia-demo",
                "display_name": "Autonom.ia Demo",
            },
            "product": {
                "id": stable_id("product:codex-na-pratica"),
                "slug": "codex-na-pratica",
                "title": "Codex na prática",
                "description": "Um curso direto para trabalhar com Codex com contexto, segurança e evidência.",
            },
            "course": {
                "id": stable_id("course:codex-na-pratica"),
                "title": "Codex na prática",
                "description": "Cinco aulas para operar Codex como parceiro de engenharia.",
            },
            "module": {"id": stable_id("module:fundamentos"), "title": "Fundamentos"},
            "offer": {
                "id": stable_id("offer:codex-na-pratica"),
                "title": "Acesso demonstrativo",
                "summary": "Acesso gratuito e não comercial à tenant de demonstração.",
            },
            "checkout": {
                "coupon_id": DEMO_COUPON_ID,
                "coupon_code": "AUTONOMIA100",
                "unit_amount_minor": 9900,
                "currency": "BRL",
            },
            "publication": {"id": stable_id("publication:codex-na-pratica:v1")},
            "entitlement": {"id": stable_id("entitlement:demo-user:codex-na-pratica")},
            "video": video,
            "lessons": manifest,
        }

        for staged_pdf, output in staged_pdfs:
            os.replace(staged_pdf, output)
        for staged_tree, output in staged_page_trees:
            _replace_directory(staged_tree, output)
        _atomic_write_text(
            MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        )
        _atomic_write_text(
            SEED_MANIFEST, json.dumps(seed, ensure_ascii=False, indent=2) + "\n"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--refresh-pages",
        action="store_true",
        help="re-rasterize committed demo pages with the current Poppler build",
    )
    main(refresh_pages=parser.parse_args().refresh_pages)
