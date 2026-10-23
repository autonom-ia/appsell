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
        # Coberto quando a pasta roda e o arquivo não está ignorado, ou quando
        # outro script com Postgres o chama pelo nome.
        foundation = POSTGRES_SCRIPTS[0].read_text(encoding="utf-8")
        others = "\n".join(path.read_text(encoding="utf-8") for path in POSTGRES_SCRIPTS[1:])
        folder_runs = "uv run --project apps/api pytest apps/api/tests \\\n" in foundation
        orphans = []
        for path in sorted(TESTS.glob("test_*.py")):
            source = path.read_text(encoding="utf-8")
            if not any(name in source for name in POSTGRES_ENV):
                continue
            relative = path.relative_to(ROOT).as_posix()
            by_folder = folder_runs and f"--ignore={relative}" not in foundation
            if not by_folder and relative not in foundation and relative not in others:
                orphans.append(relative)

        self.assertEqual([], orphans)

    def test_postgres_foundation_runs_the_folder_and_ignores_only_covered_files(
        self,
    ) -> None:
        # #450/#459: a lista à mão deixou testes de banco novos fora do CI.
        # A pasta inteira roda; o que fica de fora roda em outra chamada.
        script = POSTGRES_SCRIPTS[0].read_text(encoding="utf-8")
        scripts = "\n".join(path.read_text(encoding="utf-8") for path in POSTGRES_SCRIPTS)

        self.assertIn("uv run --project apps/api pytest apps/api/tests \\\n", script)
        ignored = [
            line.strip().removeprefix("--ignore=").removesuffix("\\").strip()
            for line in script.splitlines()
            if line.strip().startswith("--ignore=")
        ]
        self.assertTrue(ignored)
        for relative in ignored:
            with self.subTest(ignored=relative):
                self.assertTrue((ROOT / relative).is_file())
                self.assertGreaterEqual(scripts.count(relative), 2)

    def test_postgres_foundation_creates_the_reconciler_login(self) -> None:
        script = POSTGRES_SCRIPTS[0].read_text(encoding="utf-8")
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

        self.assertIn("export APPSELL_RECONCILER_PASSWORD=", script)
        self.assertIn("export APPSELL_TEST_POSTGRES_RECONCILER_URL=", script)
        self.assertIn("APPSELL_RECONCILER_PASSWORD: ${APPSELL_RECONCILER_PASSWORD:-}", compose)

    def test_postgres_foundation_makes_a_skip_fail(self) -> None:
        script = POSTGRES_SCRIPTS[0].read_text(encoding="utf-8")
        exported = script.index('export APPSELL_REQUIRE_POSTGRES="1"')
        first_pytest = script.index("uv run --project apps/api pytest")

        self.assertLess(exported, first_pytest)


if __name__ == "__main__":
    unittest.main()
