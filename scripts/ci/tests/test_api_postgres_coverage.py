import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
TESTS = ROOT / "apps" / "api" / "tests"
# Os scripts que sobem Postgres isolado e rodam pytest contra ele.
POSTGRES_SCRIPTS = (
    ROOT / "scripts" / "test-postgres-foundation.sh",
    ROOT / "scripts" / "test-storage-vertical.sh",
)
POSTGRES_ENV = (
    "APPSELL_TEST_POSTGRES_ADMIN_URL",
    "APPSELL_TEST_POSTGRES_RUNTIME_URL",
)


class ApiPostgresCoverageTests(unittest.TestCase):
    # #450: sete arquivos de teste de banco não estavam em script nenhum. No
    # passo sem Postgres eles pulavam, e no passo com Postgres não existiam.
    def test_every_postgres_test_file_runs_against_postgres(self) -> None:
        scripts = "\n".join(path.read_text(encoding="utf-8") for path in POSTGRES_SCRIPTS)
        orphans = []
        for path in sorted(TESTS.glob("test_*.py")):
            source = path.read_text(encoding="utf-8")
            if not any(name in source for name in POSTGRES_ENV):
                continue
            relative = path.relative_to(ROOT).as_posix()
            if relative not in scripts:
                orphans.append(relative)

        self.assertEqual([], orphans)

    def test_postgres_foundation_makes_a_skip_fail(self) -> None:
        script = POSTGRES_SCRIPTS[0].read_text(encoding="utf-8")
        exported = script.index('export APPSELL_REQUIRE_POSTGRES="1"')
        first_pytest = script.index("uv run --project apps/api pytest")

        self.assertLess(exported, first_pytest)


if __name__ == "__main__":
    unittest.main()
