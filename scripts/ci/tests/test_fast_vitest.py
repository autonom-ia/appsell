import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


class FastVitestContractTests(unittest.TestCase):
    def test_fast_script_uses_dedicated_config(self) -> None:
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

        self.assertEqual(
            "vitest run --config vitest.fast.config.ts",
            package["scripts"]["test:fast"],
        )

    def test_every_real_next_process_test_is_excluded_from_fast_pr_suite(self) -> None:
        config = (ROOT / "vitest.fast.config.ts").read_text(encoding="utf-8")
        real_next_tests = []
        for path in sorted((ROOT / "tests").glob("*.test.ts")):
            source = path.read_text(encoding="utf-8")
            if "node_modules/next/dist/bin/next" in source:
                real_next_tests.append(path.relative_to(ROOT).as_posix())

        self.assertTrue(real_next_tests)
        for path in real_next_tests:
            with self.subTest(path=path):
                self.assertIn(f'"{path}"', config)

        self.assertIn("fileParallelism: true", config)
        self.assertIn("maxWorkers: 4", config)

    def test_app_ci_uses_fast_suite_only_on_pr_and_full_suite_on_integrated_sha(
        self,
    ) -> None:
        workflow = (ROOT / ".github" / "workflows" / "app-ci.yml").read_text(
            encoding="utf-8"
        )
        fast_step = workflow.index("Fast web checks for pull requests")
        cert_step = workflow.index("Certify web on the integrated merge-group SHA")

        self.assertIn("pnpm test:fast", workflow[fast_step:cert_step])
        self.assertIn("pnpm test", workflow[cert_step:])


if __name__ == "__main__":
    unittest.main()
