import json
import shutil
from argparse import Namespace
from pathlib import Path

import pytest

from appsell_api import reset_demo_tenant
from appsell_api.reset_demo_tenant import DEMO_KEY, load_manifest, require_environment


def base_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSELL_DEMO_ENABLED", "true")
    monkeypatch.setenv("APPSELL_API_IDENTITY_ISSUER", "https://identity.test.autonomia.site")
    monkeypatch.setenv(
        "APPSELL_DEMO_DATABASE_URL",
        "postgresql://postgres:secret@127.0.0.1:55432/appsell",
    )


def test_reset_requires_an_explicit_local_or_ci_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_environment(monkeypatch)
    monkeypatch.delenv("APPSELL_API_ENVIRONMENT", raising=False)
    with pytest.raises(RuntimeError, match="explicitly be local or ci"):
        require_environment(Namespace(confirm_demo_key=DEMO_KEY))


def test_reset_rejects_a_non_loopback_database(monkeypatch: pytest.MonkeyPatch) -> None:
    base_environment(monkeypatch)
    monkeypatch.setenv("APPSELL_API_ENVIRONMENT", "local")
    monkeypatch.setenv(
        "APPSELL_DEMO_DATABASE_URL",
        "postgresql://postgres:secret@database.example.com/appsell",
    )
    with pytest.raises(RuntimeError, match="loopback-only"):
        require_environment(Namespace(confirm_demo_key=DEMO_KEY))


def test_reset_rejects_a_libpq_hostaddr_override(monkeypatch: pytest.MonkeyPatch) -> None:
    base_environment(monkeypatch)
    monkeypatch.setenv("APPSELL_API_ENVIRONMENT", "ci")
    monkeypatch.setenv(
        "APPSELL_DEMO_DATABASE_URL",
        "postgresql://postgres:secret@localhost/appsell?hostaddr=203.0.113.7",
    )

    with pytest.raises(RuntimeError, match="loopback-only"):
        require_environment(Namespace(confirm_demo_key=DEMO_KEY))


@pytest.mark.parametrize(
    "name",
    [
        "PGHOST",
        "PGHOSTADDR",
        "PGPORT",
        "PGDATABASE",
        "PGSERVICE",
        "PGSERVICEFILE",
        "PGSYSCONFDIR",
    ],
)
def test_reset_rejects_libpq_redirect_environment(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    base_environment(monkeypatch)
    monkeypatch.setenv("APPSELL_API_ENVIRONMENT", "local")
    monkeypatch.setenv(name, "attacker-controlled")

    with pytest.raises(RuntimeError, match="environment overrides are forbidden"):
        require_environment(Namespace(confirm_demo_key=DEMO_KEY))


def test_manifest_pins_the_committed_demo_video_bytes() -> None:
    manifest, _ = load_manifest()
    assert manifest["video"]["file_name"] == "codex-em-3-passos.mp4"


def test_manifest_rejects_video_hash_drift(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    media_root = tmp_path / "media"
    shutil.copytree(reset_demo_tenant.PDF_ROOT, media_root)
    manifest = json.loads(reset_demo_tenant.MANIFEST_PATH.read_text())
    manifest["video"]["sha256"] = "0" * 64
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    monkeypatch.setattr(reset_demo_tenant, "PDF_ROOT", media_root)
    monkeypatch.setattr(reset_demo_tenant, "MANIFEST_PATH", manifest_path)

    with pytest.raises(RuntimeError, match="video hash mismatch"):
        load_manifest()
