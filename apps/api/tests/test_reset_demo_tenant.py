from argparse import Namespace

import pytest

from appsell_api.reset_demo_tenant import DEMO_KEY, require_environment


def base_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NODE_ENV", "test")
    monkeypatch.setenv("APP_ENV", "ci")
    monkeypatch.delenv("APPSELL_DEMO_SUPERADMIN_ENABLED", raising=False)
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
