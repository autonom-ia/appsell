from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pikepdf

from .pdf_verifier import PdfRejected, _validate_pdf_structure


def main() -> int:
    if len(sys.argv) != 3:
        return 64
    source = Path(sys.argv[1])
    destination = Path(sys.argv[2])
    try:
        if not source.is_file() or destination.is_symlink():
            return 65
        source_pages = _validate_pdf_structure(source)
        with pikepdf.Pdf.open(source, attempt_recovery=False) as document:
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


if __name__ == "__main__":
    raise SystemExit(main())
