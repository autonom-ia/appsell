from __future__ import annotations

from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType


def _migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "migrations"
        / "versions"
        / "0059_canal_financial_global.py"
    )
    spec = importlib_util.spec_from_file_location("appsell_migration_0059", path)
    assert spec is not None and spec.loader is not None
    module = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_readiness_do_canal_e_verificada_nas_duas_bases() -> None:
    migration = _migration()

    assert migration._BASES_DO_READY == (
        "app_private.schema_ready_base_0016()",
        "app_private.schema_ready_base_0017()",
    )
    assert migration._BASE_DO_CONTRATO_WEBHOOK == "app_private.schema_ready_base_0016()"
    assert "resolve_webhook_endpoint(text,uuid)" in migration._READY_FUNCTIONS
