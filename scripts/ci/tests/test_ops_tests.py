import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from scripts.ci.ops_tests import MissingOpsTest, ops_test_patterns, run_all, run_pattern


def tree(files: list[str]) -> Path:
    root = Path(tempfile.mkdtemp())
    for name in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    return root


class OpsTestPatternsTests(unittest.TestCase):
    def test_each_host_script_maps_to_its_own_test(self) -> None:
        # #591: cada script Python do host roda a sua suíte no Python mais
        # baixo que os scripts declaram.
        root = tree(
            [
                "scripts/ops/cadeia_travada.py",
                "scripts/ops/alerta_de_logs.py",
                "scripts/ci/tests/test_cadeia_travada.py",
                "scripts/ci/tests/test_alerta_de_logs.py",
                "scripts/ci/tests/test_route_changes.py",
            ]
        )

        self.assertEqual(
            ["test_alerta_de_logs.py", "test_cadeia_travada.py"],
            ops_test_patterns(root),
        )

    def test_non_python_host_files_are_not_mapped(self) -> None:
        root = tree(["scripts/ops/exporta-conta.sql"])

        self.assertEqual([], ops_test_patterns(root))

    def test_host_script_without_a_test_is_refused(self) -> None:
        root = tree(["scripts/ops/novo_cron.py"])

        with self.assertRaises(MissingOpsTest) as raised:
            ops_test_patterns(root)
        self.assertIn("scripts/ops/novo_cron.py", str(raised.exception))
        self.assertIn("scripts/ci/tests/test_novo_cron.py", str(raised.exception))

    def test_missing_ops_folder_maps_nothing(self) -> None:
        self.assertEqual([], ops_test_patterns(tree([])))


def tests_dir(name: str, body: str) -> Path:
    root = Path(tempfile.mkdtemp())
    (root / name).write_text(body, encoding="utf-8")
    return root


PASSING = """import unittest


class T(unittest.TestCase):
    def test_ok(self):
        self.assertTrue(True)
"""

FAILING = """import unittest


class T(unittest.TestCase):
    def test_bad(self):
        self.assertTrue(False)
"""

EMPTY = """import unittest


class T(unittest.TestCase):
    pass
"""


def quietly(directory: Path, pattern: str) -> int:
    with contextlib.redirect_stderr(io.StringIO()):
        return run_pattern(directory, pattern)


# Cada caso usa um nome de arquivo próprio: o mesmo nome de módulo em duas
# pastas colidiria no sys.modules deste processo.
class RunPatternTests(unittest.TestCase):
    def test_passing_suite_returns_zero(self) -> None:
        self.assertEqual(0, quietly(tests_dir("test_passa.py", PASSING), "test_passa.py"))

    def test_failing_suite_returns_one(self) -> None:
        self.assertEqual(1, quietly(tests_dir("test_falha.py", FAILING), "test_falha.py"))

    def test_suite_without_tests_returns_one(self) -> None:
        # O unittest do 3.9 sai com 0 quando não roda teste nenhum: um arquivo
        # vazio ou mal nomeado passaria calado.
        self.assertEqual(1, quietly(tests_dir("test_vazio.py", EMPTY), "test_vazio.py"))

    def test_broken_import_returns_one(self) -> None:
        broken = "import modulo_que_nao_existe\n"
        self.assertEqual(1, quietly(tests_dir("test_quebrado.py", broken), "test_quebrado.py"))


class RunAllTests(unittest.TestCase):
    def test_runs_every_host_suite_and_fails_if_one_fails(self) -> None:
        root = tree(["scripts/ops/um.py", "scripts/ops/dois.py"])
        tests = root / "scripts" / "ci" / "tests"
        tests.mkdir(parents=True)
        (tests / "test_um.py").write_text(PASSING.replace("class T", "class Um"), encoding="utf-8")
        (tests / "test_dois.py").write_text(FAILING.replace("class T", "class Dois"), encoding="utf-8")

        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(1, run_all(root))

    def test_host_script_without_test_fails(self) -> None:
        root = tree(["scripts/ops/sem_teste.py"])

        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(1, run_all(root))
        self.assertIn("scripts/ops/sem_teste.py", err.getvalue())

    def test_no_host_script_passes(self) -> None:
        self.assertEqual(0, run_all(tree(["scripts/ops/exporta-conta.sql"])))


if __name__ == "__main__":
    unittest.main()
