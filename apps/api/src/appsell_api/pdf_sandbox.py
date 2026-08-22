from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pikepdf

from .pdf_verifier import PdfRejected, _validate_pdf_structure

_SANITIZABLE_ACTIVE_NAMES = frozenset({"/URI"})


def main() -> int:
    if len(sys.argv) != 3:
        return 64
    source = Path(sys.argv[1])
    destination = Path(sys.argv[2])
    try:
        if not source.is_file() or destination.is_symlink():
            return 65
        source_pages = _validate_pdf_structure(
            source, allowed_active_names=_SANITIZABLE_ACTIVE_NAMES
        )
        with pikepdf.Pdf.open(source, attempt_recovery=False) as document:
            _strip_uri_actions(document)
            document.save(
                destination,
                static_id=True,
                deterministic_id=True,
                object_stream_mode=pikepdf.ObjectStreamMode.disable,
                normalize_content=True,
                encryption=False,
            )
        os.chmod(destination, 0o600)
        normalized_pages = _validate_pdf_structure(destination)
        if normalized_pages != source_pages:
            return 66
    except (PdfRejected, pikepdf.PdfError, OSError, ValueError):
        return 67
    sys.stdout.write(json.dumps({"page_count": source_pages}, separators=(",", ":")))
    return 0


def _strip_uri_actions(document: pikepdf.Pdf) -> None:
    """Remove passive web links before the strict normalized-PDF validation."""
    stack = [document.Root]
    stack.extend(document.objects)
    seen_indirect: set[tuple[int, int]] = set()
    inspected = 0
    while stack:
        item = stack.pop()
        object_generation = getattr(item, "objgen", (0, 0))
        if object_generation != (0, 0):
            if object_generation in seen_indirect:
                continue
            seen_indirect.add(object_generation)
        inspected += 1
        if inspected > 20_000:
            raise PdfRejected("PDF object graph is too large")
        if isinstance(item, pikepdf.Dictionary):
            if len(stack) + len(item) > 20_000:
                raise PdfRejected("PDF object graph is too large")
            if str(item.get("/S")) == "/URI":
                del item["/S"]
            if "/URI" in item:
                del item["/URI"]
            stack.extend(item[name] for name in list(item.keys()))
        elif isinstance(item, pikepdf.Array):
            if len(stack) + len(item) > 20_000:
                raise PdfRejected("PDF object graph is too large")
            stack.extend(item)


if __name__ == "__main__":
    raise SystemExit(main())
