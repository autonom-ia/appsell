from __future__ import annotations

import hashlib
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, cast
from urllib.parse import urlsplit, urlunsplit

import httpx
import pikepdf

from .pdf_verifier import MAX_PDF_BYTES, _validate_pdf_structure

#: O que o leitor abre no player, em vez de mandar o aluno para outra aba.
#:
#: Eram `.mov` e `.mp4`. Qualquer outro container caia como link externo e
#: abria fora da area do aluno — um `.mkv` gravado pelo vendedor virava um
#: download bruto. O servidor prepara um MP4 apresentavel a partir de qualquer
#: um destes, entao a lista pode ser a dos containers que o `ffmpeg` le.
VIDEO_EXTENSIONS = (
    ".mov",
    ".mp4",
    ".m4v",
    ".webm",
    ".mkv",
    ".avi",
    ".mpg",
    ".mpeg",
    ".wmv",
    ".flv",
    ".3gp",
    ".ogv",
    ".mts",
    ".m2ts",
    ".ts",
)

MAX_PDF_LINKS = 512
MAX_PDF_LINK_ANNOTATIONS = 2_048
MAX_PDF_LINK_URL_CHARACTERS = 2_048
_LINK_ID = re.compile(r"[0-9a-f]{16}\Z")


class PdfLinksRejected(ValueError):
    pass


class PdfLinksUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class _Candidate:
    page: int
    kind: str
    target: str | int
    left: float
    top: float
    width: float
    height: float

    @property
    def area(self) -> float:
        return self.width * self.height


def read_remote_pdf_link_manifest(
    source: BinaryIO,
    *,
    expected_size: int,
    expected_sha256_hex: str,
    expected_page_count: int,
) -> dict[str, Any]:
    if (
        not 1 <= expected_size <= MAX_PDF_BYTES
        or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256_hex)
        or not 1 <= expected_page_count <= 100
    ):
        raise PdfLinksRejected("PDF link request metadata is invalid")
    remote_url = os.environ.get("APPSELL_API_PDF_SANDBOX_URL", "").strip()
    remote_socket = os.environ.get("APPSELL_API_PDF_SANDBOX_SOCKET", "").strip()
    transport: httpx.BaseTransport | None = None
    base_url = ""
    if remote_socket:
        transport = httpx.HTTPTransport(uds=remote_socket, retries=0)
        base_url = "http://pdf-sandbox"
    elif remote_url:
        parsed = httpx.URL(remote_url)
        base_url = f"{parsed.scheme}://{parsed.host}"
        if parsed.port is not None:
            base_url += f":{parsed.port}"
    else:
        raise PdfLinksUnavailable("isolated PDF link service is not configured")
    try:
        with httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(30.0, connect=3.0),
            trust_env=False,
            follow_redirects=False,
        ) as client:
            response = client.post(
                f"{base_url}/links",
                headers={
                    "content-type": "application/pdf",
                    "x-appsell-byte-size": str(expected_size),
                    "x-appsell-sha256": expected_sha256_hex,
                },
                content=source,
            )
    except (httpx.HTTPError, OSError) as error:
        raise PdfLinksUnavailable("isolated PDF link service request failed") from error
    if response.status_code in {413, 415, 422}:
        raise PdfLinksRejected("isolated PDF link service rejected the document")
    if response.status_code != 200 or response.headers.get("content-type") != "application/json":
        raise PdfLinksUnavailable("isolated PDF link service is unavailable")
    if len(response.content) > 256 * 1024:
        raise PdfLinksUnavailable("isolated PDF link response exceeds its limit")
    try:
        payload = json.loads(response.content)
    except json.JSONDecodeError as error:
        raise PdfLinksUnavailable("isolated PDF link response is invalid") from error
    if not valid_pdf_link_manifest(payload, expected_page_count=expected_page_count):
        raise PdfLinksUnavailable("isolated PDF link response contract is invalid")
    return cast(dict[str, Any], payload)


def extract_pdf_link_manifest(
    path: Path,
    *,
    expected_size: int,
    expected_sha256_hex: str,
) -> dict[str, Any]:
    if (
        not path.is_file()
        or path.is_symlink()
        or not 1 <= expected_size <= MAX_PDF_BYTES
        or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256_hex)
    ):
        raise PdfLinksRejected("PDF link source metadata is invalid")
    try:
        if path.stat().st_size != expected_size or _sha256(path) != expected_sha256_hex:
            raise PdfLinksRejected("PDF link source integrity does not match metadata")
        page_count = _validate_pdf_structure(path)
        with pikepdf.Pdf.open(path, attempt_recovery=False) as document:
            if len(document.pages) != page_count:
                raise PdfLinksRejected("PDF link page count drifted")
            links = _document_links(document)
    except PdfLinksRejected:
        raise
    except (OSError, pikepdf.PdfError, TypeError, ValueError) as error:
        raise PdfLinksRejected("PDF links could not be inspected") from error
    return {"schema_version": 1, "page_count": page_count, "links": links}


def valid_pdf_link_manifest(value: object, *, expected_page_count: int) -> bool:
    if not isinstance(value, dict) or set(value) != {"schema_version", "page_count", "links"}:
        return False
    if value.get("schema_version") != 1 or value.get("page_count") != expected_page_count:
        return False
    links = value.get("links")
    if not isinstance(links, list) or len(links) > MAX_PDF_LINKS:
        return False
    return all(_valid_link(link, expected_page_count) for link in links)


def _document_links(document: pikepdf.Pdf) -> list[dict[str, Any]]:
    page_map = {page.obj.objgen: number for number, page in enumerate(document.pages, start=1)}
    named_destinations = _named_destinations(document)
    candidates: list[_Candidate] = []
    annotations_seen = 0
    for page_number, page in enumerate(document.pages, start=1):
        page_box = _page_box(page.obj)
        rotation = _page_rotation(page.obj)
        annotations = page.obj.get("/Annots")
        if annotations is None:
            continue
        if not isinstance(annotations, pikepdf.Array):
            raise PdfLinksRejected("PDF page annotations are invalid")
        for annotation in annotations:
            if not isinstance(annotation, pikepdf.Dictionary):
                raise PdfLinksRejected("PDF link annotation is invalid")
            if str(annotation.get("/Subtype")) != "/Link":
                continue
            annotations_seen += 1
            if annotations_seen > MAX_PDF_LINK_ANNOTATIONS:
                raise PdfLinksRejected("PDF contains too many link annotations")
            rect = _normalized_rect(annotation.get("/Rect"), page_box, rotation)
            if rect is None:
                continue
            target_page = _annotation_page(
                annotation,
                named_destinations=named_destinations,
                page_map=page_map,
            )
            if target_page is not None:
                candidates.append(_Candidate(page_number, "page", target_page, *rect))
                continue
            target_url = _safe_https_url(annotation.get("/Contents"))
            if target_url is None:
                continue
            path = urlsplit(target_url).path.lower()
            kind = "video" if path.endswith(VIDEO_EXTENSIONS) else "external"
            candidates.append(_Candidate(page_number, kind, target_url, *rect))

    links: list[dict[str, Any]] = []
    grouped: dict[tuple[int, str, str | int], list[_Candidate]] = defaultdict(list)
    for candidate in candidates:
        grouped[(candidate.page, candidate.kind, candidate.target)].append(candidate)
    for key in sorted(grouped, key=lambda item: (item[0], item[1], str(item[2]))):
        for candidate in _remove_contained_rectangles(grouped[key]):
            link_id = hashlib.sha256(
                (
                    f"{candidate.page}:{candidate.kind}:{candidate.target}:"
                    f"{candidate.left:.4f}:{candidate.top:.4f}:"
                    f"{candidate.width:.4f}:{candidate.height:.4f}"
                ).encode()
            ).hexdigest()[:16]
            link: dict[str, Any] = {
                "id": link_id,
                "page": candidate.page,
                "kind": candidate.kind,
                "rect": {
                    "left": candidate.left,
                    "top": candidate.top,
                    "width": candidate.width,
                    "height": candidate.height,
                },
            }
            if candidate.kind == "page":
                link["target_page"] = candidate.target
            else:
                link["target_url"] = candidate.target
            links.append(link)
            if len(links) > MAX_PDF_LINKS:
                raise PdfLinksRejected("PDF contains too many usable links")
    return links


def _named_destinations(document: pikepdf.Pdf) -> dict[str, object]:
    result: dict[str, object] = {}
    root_names = document.Root.get("/Names")
    if isinstance(root_names, pikepdf.Dictionary):
        destinations = root_names.get("/Dests")
        if isinstance(destinations, pikepdf.Dictionary):
            stack = [destinations]
            visits = 0
            while stack:
                node = stack.pop()
                visits += 1
                if visits > 2_048:
                    raise PdfLinksRejected("PDF destination tree is too large")
                names = node.get("/Names")
                if names is not None:
                    if not isinstance(names, pikepdf.Array) or len(names) % 2:
                        raise PdfLinksRejected("PDF destination names are invalid")
                    for index in range(0, len(names), 2):
                        result[str(names[index])] = names[index + 1]
                children = node.get("/Kids")
                if children is None:
                    continue
                if not isinstance(children, pikepdf.Array):
                    raise PdfLinksRejected("PDF destination tree is invalid")
                stack.extend(child for child in children if isinstance(child, pikepdf.Dictionary))
    legacy = document.Root.get("/Dests")
    if isinstance(legacy, pikepdf.Dictionary):
        for name in legacy.keys():
            result[str(name).lstrip("/")] = legacy[name]
    return result


def _annotation_page(
    annotation: pikepdf.Dictionary,
    *,
    named_destinations: dict[str, object],
    page_map: dict[tuple[int, int], int],
) -> int | None:
    destination: object | None = annotation.get("/Dest")
    action = annotation.get("/A")
    if isinstance(action, pikepdf.Dictionary) and str(action.get("/S")) == "/GoTo":
        destination = action.get("/D")
    for _ in range(8):
        if isinstance(destination, pikepdf.Dictionary):
            destination = destination.get("/D")
            continue
        if isinstance(destination, (pikepdf.String, pikepdf.Name)):
            destination = named_destinations.get(str(destination).lstrip("/"))
            continue
        break
    if not isinstance(destination, pikepdf.Array) or not destination:
        return None
    first = destination[0]
    page_number = page_map.get(getattr(first, "objgen", (0, 0)))
    if page_number is not None:
        return page_number
    if isinstance(first, int) and 0 <= first < len(page_map):
        return first + 1
    return None


def _page_box(page: pikepdf.Dictionary) -> tuple[float, float, float, float]:
    box = page.get("/CropBox") or page.get("/MediaBox")
    if not isinstance(box, pikepdf.Array) or len(box) != 4:
        raise PdfLinksRejected("PDF page box is invalid")
    left, bottom, right, top = (float(value) for value in box)
    if (
        right <= left
        or top <= bottom
        or max(abs(value) for value in (left, bottom, right, top)) > 1e7
    ):
        raise PdfLinksRejected("PDF page box is invalid")
    return left, bottom, right, top


def _page_rotation(page: pikepdf.Dictionary) -> int:
    try:
        rotation = int(page.get("/Rotate") or 0) % 360
    except (TypeError, ValueError) as error:
        raise PdfLinksRejected("PDF page rotation is invalid") from error
    if rotation not in {0, 90, 180, 270}:
        raise PdfLinksRejected("PDF page rotation is invalid")
    return rotation


def _normalized_rect(
    raw_rect: object,
    page_box: tuple[float, float, float, float],
    rotation: int,
) -> tuple[float, float, float, float] | None:
    if not isinstance(raw_rect, pikepdf.Array) or len(raw_rect) != 4:
        return None
    try:
        first_x, first_y, second_x, second_y = (float(value) for value in raw_rect)
    except (TypeError, ValueError):
        return None
    left, bottom, right, top = page_box
    width = right - left
    height = top - bottom
    pdf_left = min(max(min(first_x, second_x), left), right) - left
    pdf_right = min(max(max(first_x, second_x), left), right) - left
    pdf_bottom = min(max(min(first_y, second_y), bottom), top) - bottom
    pdf_top = min(max(max(first_y, second_y), bottom), top) - bottom
    if pdf_right - pdf_left <= 0 or pdf_top - pdf_bottom <= 0:
        return None
    corners = [
        _display_point(x, y, width, height, rotation)
        for x in (pdf_left, pdf_right)
        for y in (pdf_bottom, pdf_top)
    ]
    display_width, display_height = (width, height) if rotation in {0, 180} else (height, width)
    xs = [point[0] for point in corners]
    ys = [point[1] for point in corners]
    normalized = (
        _percent(min(xs), display_width),
        _percent(min(ys), display_height),
        _percent(max(xs) - min(xs), display_width),
        _percent(max(ys) - min(ys), display_height),
    )
    if normalized[2] < 0.05 or normalized[3] < 0.05:
        return None
    return normalized


def _display_point(
    x: float, y: float, width: float, height: float, rotation: int
) -> tuple[float, float]:
    if rotation == 0:
        return x, height - y
    if rotation == 90:
        return y, x
    if rotation == 180:
        return width - x, y
    return height - y, width - x


def _remove_contained_rectangles(candidates: list[_Candidate]) -> list[_Candidate]:
    kept: list[_Candidate] = []
    for candidate in sorted(candidates, key=lambda value: value.area, reverse=True):
        if any(_contains(existing, candidate) for existing in kept):
            continue
        kept.append(candidate)
    return sorted(kept, key=lambda value: (value.top, value.left))


def _contains(outer: _Candidate, inner: _Candidate) -> bool:
    tolerance = 0.05
    return (
        outer.left <= inner.left + tolerance
        and outer.top <= inner.top + tolerance
        and outer.left + outer.width + tolerance >= inner.left + inner.width
        and outer.top + outer.height + tolerance >= inner.top + inner.height
    )


def _safe_https_url(value: object) -> str | None:
    if not isinstance(value, pikepdf.String):
        return None
    raw = str(value).strip()
    if not 1 <= len(raw) <= MAX_PDF_LINK_URL_CHARACTERS or any(ord(char) < 32 for char in raw):
        return None
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    return urlunsplit(("https", parsed.netloc.lower(), parsed.path, parsed.query, parsed.fragment))


def _valid_link(value: object, page_count: int) -> bool:
    if not isinstance(value, dict):
        return False
    common = {"id", "page", "kind", "rect"}
    kind = value.get("kind")
    expected = common | ({"target_page"} if kind == "page" else {"target_url"})
    if (
        set(value) != expected
        or not isinstance(value.get("id"), str)
        or not _LINK_ID.fullmatch(value["id"])
    ):
        return False
    if type(value.get("page")) is not int or not 1 <= value["page"] <= page_count:
        return False
    if kind == "page":
        if type(value.get("target_page")) is not int or not 1 <= value["target_page"] <= page_count:
            return False
    elif kind in {"video", "external"}:
        if (
            not isinstance(value.get("target_url"), str)
            or _safe_plain_https_url(value["target_url"]) is None
        ):
            return False
    else:
        return False
    rect = value.get("rect")
    return (
        isinstance(rect, dict)
        and set(rect) == {"left", "top", "width", "height"}
        and all(
            type(rect.get(name)) in {float, int} and 0 <= float(rect[name]) <= 100
            for name in ("left", "top", "width", "height")
        )
        and float(rect["width"]) > 0
        and float(rect["height"]) > 0
        and (
            float(rect["left"]) + float(rect["width"]) <= 100.01
            and float(rect["top"]) + float(rect["height"]) <= 100.01
        )
    )


def _safe_plain_https_url(value: str) -> str | None:
    if not 1 <= len(value) <= MAX_PDF_LINK_URL_CHARACTERS:
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    return value


def _percent(value: float, total: float) -> float:
    return round(min(100.0, max(0.0, value * 100 / total)), 4)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(64 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
