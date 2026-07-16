import json
import shutil
from argparse import Namespace
from pathlib import Path

import pytest

from appsell_api import reset_demo_tenant
from appsell_api.reset_demo_tenant import DEMO_KEY, load_manifest, require_environment


def base_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NODE_ENV", "test")
    monkeypatch.setenv("APP_ENV", "ci")
    monkeypatch.delenv("APPSELL_DEMO_SUPERADMIN_ENABLED", raising=False)
    monkeypatch.setenv("APPSELL_DEMO_ENABLED", "true")
    monkeypatch.setenv("APPSELL_API_IDENTITY_ISSUER", "https://identity.test.autonomia.site")
    monkeypatch.delenv("APPSELL_DEMO_REMOTE_RESET_SCOPE", raising=False)
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


@pytest.mark.parametrize("node_environment", ["", "production", "preview", "staging"])
def test_reset_rejects_node_outside_the_explicit_allowlist(
    monkeypatch: pytest.MonkeyPatch,
    node_environment: str,
) -> None:
    base_environment(monkeypatch)
    monkeypatch.setenv("NODE_ENV", node_environment)
    monkeypatch.setenv("APPSELL_API_ENVIRONMENT", "local")

    with pytest.raises(RuntimeError, match="development or test"):
        require_environment(Namespace(confirm_demo_key=DEMO_KEY))


@pytest.mark.parametrize("app_environment", ["", "preview", "staging", "production"])
def test_reset_rejects_app_environment_outside_local_ci(
    monkeypatch: pytest.MonkeyPatch,
    app_environment: str,
) -> None:
    base_environment(monkeypatch)
    monkeypatch.setenv("APP_ENV", app_environment)
    monkeypatch.setenv("APPSELL_API_ENVIRONMENT", "ci")

    with pytest.raises(RuntimeError, match="local or ci"):
        require_environment(Namespace(confirm_demo_key=DEMO_KEY))


def test_reset_requires_an_explicit_superadmin_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_environment(monkeypatch)
    monkeypatch.setenv("APPSELL_API_ENVIRONMENT", "ci")
    monkeypatch.setenv("APPSELL_DEMO_SUPERADMIN_ENABLED", "sometimes")

    with pytest.raises(RuntimeError, match="explicitly be true or false"):
        require_environment(Namespace(confirm_demo_key=DEMO_KEY))

    monkeypatch.setenv("APPSELL_DEMO_SUPERADMIN_ENABLED", "true")
    _issuer, _database_url, provision_superadmin = require_environment(
        Namespace(confirm_demo_key=DEMO_KEY)
    )
    assert provision_superadmin is True


def test_reset_rejects_a_non_loopback_database(monkeypatch: pytest.MonkeyPatch) -> None:
    base_environment(monkeypatch)
    monkeypatch.setenv("APPSELL_API_ENVIRONMENT", "local")
    monkeypatch.setenv(
        "APPSELL_DEMO_DATABASE_URL",
        "postgresql://postgres:secret@database.example.com/appsell",
    )
    with pytest.raises(RuntimeError, match="loopback-only or the exact runtime-preview-77 target"):
        require_environment(Namespace(confirm_demo_key=DEMO_KEY))


def exact_remote_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    base_environment(monkeypatch)
    monkeypatch.setenv("NODE_ENV", "test")
    monkeypatch.setenv("APP_ENV", "ci")
    monkeypatch.setenv("APPSELL_API_ENVIRONMENT", "ci")
    monkeypatch.setenv("APPSELL_API_IDENTITY_ISSUER", "https://identity.runtime-preview.local")
    monkeypatch.setenv("APPSELL_DEMO_REMOTE_RESET_SCOPE", "appsell-runtime-preview-77")
    monkeypatch.setenv(
        "APPSELL_DEMO_DATABASE_URL",
        "postgresql://appsell_preview_77_migrator:secret@"
        "sell-autonomia_postgres:5432/appsell_preview_77",
    )


def test_reset_accepts_only_the_exact_remote_runtime_preview_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exact_remote_environment(monkeypatch)
    _issuer, database_url, _provision_superadmin = require_environment(
        Namespace(confirm_demo_key=DEMO_KEY)
    )
    assert "sell-autonomia_postgres:5432/appsell_preview_77" in database_url


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("APPSELL_DEMO_REMOTE_RESET_SCOPE", "another-preview"),
        ("NODE_ENV", "development"),
        ("APP_ENV", "local"),
        ("APPSELL_API_ENVIRONMENT", "local"),
        ("APPSELL_API_IDENTITY_ISSUER", "https://identity.example.test"),
    ],
)
def test_remote_reset_rejects_any_scope_or_environment_drift(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    exact_remote_environment(monkeypatch)
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError):
        require_environment(Namespace(confirm_demo_key=DEMO_KEY))


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://appsell_preview_77_migrator:secret@database.example.com:5432/appsell_preview_77",
        "postgresql://appsell_preview_77_migrator:secret@sell-autonomia_postgres:55432/appsell_preview_77",
        "postgresql://appsell_preview_77_migrator:secret@sell-autonomia_postgres:5432/another_database",
        "postgresql://postgres:secret@sell-autonomia_postgres:5432/appsell_preview_77",
        "postgresql://appsell_preview_77_migrator:secret@sell-autonomia_postgres,localhost:5432/appsell_preview_77",
        "postgresql://appsell_preview_77_migrator:secret@sell-autonomia_postgres:5432/apps"
        "ell_preview_77?hostaddr=203.0.113.7",
        "postgresql://appsell_preview_77_migrator:secret@sell-autonomia_postgres:5432/apps"
        "ell_preview_77?service=attacker",
    ],
)
def test_remote_reset_rejects_any_database_target_drift(
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
) -> None:
    exact_remote_environment(monkeypatch)
    monkeypatch.setenv("APPSELL_DEMO_DATABASE_URL", database_url)
    with pytest.raises(RuntimeError, match="exact runtime-preview-77 target"):
        require_environment(Namespace(confirm_demo_key=DEMO_KEY))


def test_loopback_reset_rejects_remote_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    base_environment(monkeypatch)
    monkeypatch.setenv("APPSELL_API_ENVIRONMENT", "ci")
    monkeypatch.setenv("APPSELL_DEMO_REMOTE_RESET_SCOPE", "appsell-runtime-preview-77")
    with pytest.raises(RuntimeError, match="forbidden for a loopback"):
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
