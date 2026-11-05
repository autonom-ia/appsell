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
