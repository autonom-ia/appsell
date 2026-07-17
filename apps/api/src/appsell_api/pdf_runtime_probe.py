from __future__ import annotations

import json
import os
from pathlib import Path

from .pdf_derivatives import prepared_pdf_file

_PROBE_PDF = "01-o-que-e-codex.pdf"


def main() -> None:
    root_value = os.environ.get("APPSELL_API_PRIVATE_LEARNING_ASSET_ROOT", "").strip()
    if not root_value:
        raise RuntimeError("private learning asset root is unavailable")
    root = Path(root_value).resolve(strict=True)
    candidate = root / _PROBE_PDF
    if candidate.is_symlink():
        raise RuntimeError("runtime PDF probe source is invalid")
    source = candidate.resolve(strict=True)
    if source.parent != root or not source.is_file():
        raise RuntimeError("runtime PDF probe source is invalid")
    with prepared_pdf_file(source) as prepared:
        if prepared.page_count < 1 or not prepared.pages:
            raise RuntimeError("runtime PDF probe returned no protected pages")
        result = {
            "page_count": prepared.page_count,
            "source_sha256": prepared.source_sha256_hex,
            "status": "ok",
        }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
