import tempfile
import unittest
from pathlib import Path

from scripts.ci.check_migration_chain import ChainError, check_chain, main

ROOT = Path(__file__).resolve().parents[3]


def migration(revision: str, down: str | None, *, annotated: bool = False) -> str:
    down_literal = "None" if down is None else repr(down)
    if annotated:
        return f'revision: str = "{revision}"\ndown_revision: str | None = {down_literal}\n'
    return f'revision = "{revision}"\ndown_revision = {down_literal}\n'


class ChainTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.versions = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, name: str, source: str) -> None:
        (self.versions / name).write_text(source, encoding="utf-8")

    def linear(self) -> None:
        self.write("0001_base.py", migration("0001_base", None, annotated=True))
        self.write("0002_tabela.py", migration("0002_tabela", "0001_base"))
        self.write("0003_coluna.py", migration("0003_coluna", "0002_tabela"))

    def test_linear_chain_passes_and_reports_its_head(self) -> None:
        self.linear()

        self.assertEqual("0003_coluna", check_chain(self.versions))

    def test_two_migrations_with_the_same_number_fail(self) -> None:
        # Catches: two green PRs each adding 0004, merged one after the other.
        self.linear()
        self.write("0004_a.py", migration("0004_a", "0003_coluna"))
        self.write("0004_b.py", migration("0004_b", "0004_a"))

        with self.assertRaises(ChainError) as erro:
            check_chain(self.versions)
        self.assertIn("0004", str(erro.exception))

    def test_two_heads_fail(self) -> None:
        # Catches: two PRs branching from the same parent with different numbers.
        self.linear()
        self.write("0004_a.py", migration("0004_a", "0003_coluna"))
        self.write("0005_b.py", migration("0005_b", "0003_coluna"))

        with self.assertRaises(ChainError) as erro:
            check_chain(self.versions)
        self.assertIn("0004_a", str(erro.exception))
        self.assertIn("0005_b", str(erro.exception))

    def test_a_parent_that_does_not_exist_fails(self) -> None:
        self.linear()
        self.write("0004_orfa.py", migration("0004_orfa", "0003_apagada"))

        with self.assertRaises(ChainError) as erro:
            check_chain(self.versions)
        self.assertIn("0003_apagada", str(erro.exception))

    def test_a_revision_with_another_number_than_the_file_fails(self) -> None:
        # The number lives in the file name; a revision that disagrees hides it.
        self.linear()
        self.write("0004_nome.py", migration("0005_outro", "0003_coluna"))

        with self.assertRaises(ChainError):
            check_chain(self.versions)

    def test_a_shortened_revision_with_the_same_number_passes(self) -> None:
        # As in 0028 and 0030: the file name is longer than the revision.
        self.linear()
        self.write("0004_um_nome_bem_mais_comprido_que_cabe.py", migration("0004_nome_curto", "0003_coluna"))

        self.assertEqual("0004_nome_curto", check_chain(self.versions))

    def test_a_revision_longer_than_alembic_version_fails(self) -> None:
        # Catches: the upgrade running the DDL and then failing to stamp it.
        self.linear()
        longa = "0004_" + "x" * 28
        self.write(f"{longa}.py", migration(longa, "0003_coluna"))

        with self.assertRaises(ChainError) as erro:
            check_chain(self.versions)
        self.assertIn("32", str(erro.exception))

    def test_a_merge_migration_closes_two_branches_into_one_head(self) -> None:
        self.linear()
        self.write("0004_a.py", migration("0004_a", "0003_coluna"))
        self.write("0005_b.py", migration("0005_b", "0003_coluna"))
        self.write(
            "0006_junta.py",
            'revision = "0006_junta"\ndown_revision = ("0004_a", "0005_b")\n',
        )

        self.assertEqual("0006_junta", check_chain(self.versions))

    def test_a_cycle_fails_even_with_one_root_and_one_head(self) -> None:
        # Catches: 0003 -> 0005 -> 0004 -> 0003 hangs off nothing; 0002 looks
        # like the only head and alembic raises CycleDetected on upgrade.
        self.write("0001_base.py", migration("0001_base", None))
        self.write("0002_tabela.py", migration("0002_tabela", "0001_base"))
        self.write("0003_coluna.py", migration("0003_coluna", "0005_b"))
        self.write("0004_a.py", migration("0004_a", "0003_coluna"))
        self.write("0005_b.py", migration("0005_b", "0004_a"))

        with self.assertRaises(ChainError) as erro:
            check_chain(self.versions)
        self.assertIn("0003_coluna", str(erro.exception))

    def test_a_cycle_reachable_from_the_head_fails(self) -> None:
        # Catches: 0002 <-> 0003 hanging under the head, and a self-loop; both
        # descend from the head, and alembic still raises CycleDetected.
        cases = {
            "ida_e_volta": {
                "0001_r.py": migration("0001_r", None),
                "0002_a.py": 'revision = "0002_a"\ndown_revision = ("0003_b", "0001_r")\n',
                "0003_b.py": migration("0003_b", "0002_a"),
                "0004_h.py": migration("0004_h", "0002_a"),
            },
            "auto_laco": {
                "0001_r.py": migration("0001_r", None),
                "0002_a.py": 'revision = "0002_a"\ndown_revision = ("0002_a", "0001_r")\n',
                "0003_h.py": migration("0003_h", "0002_a"),
            },
        }
        for nome, files in cases.items():
            with self.subTest(nome=nome), tempfile.TemporaryDirectory() as folder:
                for name, source in files.items():
                    (Path(folder) / name).write_text(source, encoding="utf-8")
                with self.assertRaises(ChainError) as erro:
                    check_chain(Path(folder))
                self.assertIn("ciclo", str(erro.exception))

    def test_dynamic_writes_fail_because_they_cannot_be_checked(self) -> None:
        for nome, extra in {
            "globals": 'globals()["revision"] = "0004_z"\n',
            "vars": 'vars()["revision"] = "0004_z"\n',
            "setattr": 'import sys\nsetattr(sys.modules[__name__], "revision", "0004_z")\n',
            "exec": 'exec("revision = 1")\n',
        }.items():
            with self.subTest(nome=nome):
                self.linear()
                self.write("0004_a.py", migration("0004_a", "0003_coluna") + extra)
                with self.assertRaises(ChainError):
                    check_chain(self.versions)

    def test_any_other_binding_of_the_names_fails_closed(self) -> None:
        # Fail-closed on purpose: a local variable named revision is a visible,
        # cheap false red (rename it); modelling scopes left silent false greens.
        for nome, extra in {
            "local": "def upgrade():\n    for revision in range(3):\n        pass\n",
            "walrus_em_lambda": 'f = lambda a=(revision := "0004_z"): a\n',
            "anotacao": 'def f(a: (revision := "0004_z")):\n    pass\n',
            "del": "del revision\n",
            "comprehension": "xs = [revision for revision in range(3)]\n",
            "import_estrela": "from os.path import *\n",
            "match_mapping": "match {}:\n    case {**revision}:\n        pass\n",
        }.items():
            with self.subTest(nome=nome):
                self.linear()
                self.write("0004_a.py", migration("0004_a", "0003_coluna") + extra)
                with self.assertRaises(ChainError) as erro:
                    check_chain(self.versions)
                self.assertIn("renomeie", str(erro.exception))

    def test_rebinding_revision_by_def_import_or_class_fails(self) -> None:
        for nome, extra in {
            "def": "def revision():\n    pass\n",
            "import": "import os as revision\n",
            "class": "class revision:\n    pass\n",
            "global": 'def f():\n    global revision\n    revision = "0004_z"\nf()\n',
        }.items():
            with self.subTest(nome=nome):
                self.linear()
                self.write("0004_a.py", migration("0004_a", "0003_coluna") + extra)
                with self.assertRaises(ChainError):
                    check_chain(self.versions)

    def test_a_revision_assigned_outside_the_top_level_fails(self) -> None:
        # Catches: the checker reading one value while alembic runs the module
        # and sees another one set inside an `if`.
        self.linear()
        self.write(
            "0004_a.py",
            'revision = "0004_a"\ndown_revision = "0003_coluna"\n'
            'if True:\n    revision = "0004_z"\n',
        )

        with self.assertRaises(ChainError):
            check_chain(self.versions)

    def test_a_revision_assigned_twice_fails(self) -> None:
        self.linear()
        self.write(
            "0004_a.py",
            'revision = "0004_a"\nrevision = "0004_b"\ndown_revision = "0003_coluna"\n',
        )

        with self.assertRaises(ChainError):
            check_chain(self.versions)

    def test_the_same_number_written_with_other_zeros_fails(self) -> None:
        # Catches: 5_x next to 0005_b; as strings they differ, as numbers not.
        self.linear()
        self.write("0004_a.py", migration("0004_a", "0003_coluna"))
        self.write("0005_b.py", migration("0005_b", "0004_a"))
        self.write("5_x.py", migration("5_x", "0005_b"))

        with self.assertRaises(ChainError) as erro:
            check_chain(self.versions)
        self.assertIn("repetido", str(erro.exception))

    def test_what_cannot_be_read_fails_instead_of_passing(self) -> None:
        # "Não deu para checar" counts as failure, never as approval.
        cases = {
            "sintaxe": "revision = (\n",
            "sem_revision": 'down_revision = "0003_coluna"\n',
            "revision_calculada": 'revision = "0004_" + "x"\ndown_revision = "0003_coluna"\n',
        }
        for nome, source in cases.items():
            with self.subTest(nome=nome):
                self.linear()
                self.write("0004_x.py", source)
                with self.assertRaises(ChainError):
                    check_chain(self.versions)

    def test_an_empty_or_missing_directory_fails(self) -> None:
        with self.assertRaises(ChainError):
            check_chain(self.versions)
        with self.assertRaises(ChainError):
            check_chain(self.versions / "nao-existe")

    def test_main_exits_1_with_an_error_annotation(self) -> None:
        self.linear()
        self.write("0003_dupla.py", migration("0003_dupla", "0002_tabela"))

        self.assertEqual(1, main([str(self.versions)]))
        self.assertEqual(0, main([str(ROOT / "apps" / "api" / "migrations" / "versions")]))


class RepositoryChainTests(unittest.TestCase):
    def test_the_repository_chain_has_one_head_and_unique_numbers(self) -> None:
        head = check_chain(ROOT / "apps" / "api" / "migrations" / "versions")

        self.assertTrue(head[:4].isdigit())


if __name__ == "__main__":
    unittest.main()
