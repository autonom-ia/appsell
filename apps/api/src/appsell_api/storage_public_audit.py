from __future__ import annotations

import json

from .config import Settings
from .storage_s3 import S3Storage


def main() -> None:
    """Audit the real browser-facing storage edge from an external runner."""
    settings = Settings()
    if not settings.storage_configured:
        raise RuntimeError("S3 storage is not configured")
    storage = S3Storage(settings)
    storage.assert_public_edge_controls(
        require_tls=settings.environment in {"staging", "production"},
        cors_mode=settings.storage_cors_mode,
        allowed_origin=settings.storage_allowed_origin,
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "public_endpoint": storage.public_endpoint,
                "anonymous_access": "denied",
            },
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
