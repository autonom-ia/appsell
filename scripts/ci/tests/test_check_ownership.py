import json
import tempfile
import unittest
from pathlib import Path

from scripts.ci.check_ownership import (
    ManifestError,
    check_pull_request,
    load_manifest,
    main,
    trilhas_tokens,
)

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "docs" / "programa" / "posse.json"
TRILHAS = ROOT / "docs" / "programa" / "TRILHAS.md"

SMALL = {
    "trilhas": {
        "trilha:a": {"": ["app/a/", "lib/a-*"], "app/components/": ["a.tsx"]},
        "trilha:b": {"": ["app/a/b/", "lib/b.ts"]},
    },
    "excecoes": {"": ["tests/", "package.json"]},
}


class OwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = load_manifest(SMALL)

    def test_files_of_the_labelled_trilha_pass(self) -> None:
        result = check_pull_request(self.manifest, ["app/a/x.tsx", "lib/a-y.ts"], ["trilha:a"])

        self.assertEqual([], result.failures)
        self.assertEqual([], result.warnings)

    def test_a_file_of_another_trilha_without_its_label_fails(self) -> None:
        # Catches: trilha A editing trilha B's folder with only its own label.
        result = check_pull_request(self.manifest, ["app/a/x.tsx", "lib/b.ts"], ["trilha:a"])

        self.assertEqual(["trilha:b: lib/b.ts"], result.failures)

    def test_both_labels_allow_touching_both_trilhas(self) -> None:
        result = check_pull_request(self.manifest, ["app/a/x.tsx", "lib/b.ts"], ["trilha:a", "trilha:b"])

        self.assertEqual([], result.failures)

    def test_the_most_specific_pattern_wins(self) -> None:
        # app/a/b/ belongs to B even though it sits inside A's app/a/.
        result = check_pull_request(self.manifest, ["app/a/b/z.tsx"], ["trilha:a"])

        self.assertEqual(["trilha:b: app/a/b/z.tsx"], result.failures)

    def test_a_base_folder_prefixes_bare_names(self) -> None:
        result = check_pull_request(self.manifest, ["app/components/a.tsx"], ["trilha:b"])

        self.assertEqual(["trilha:a: app/components/a.tsx"], result.failures)

    def test_a_pull_request_without_label_fails_on_owned_files(self) -> None:
        result = check_pull_request(self.manifest, ["lib/b.ts"], [])

        self.assertEqual(["trilha:b: lib/b.ts"], result.failures)

    def test_exceptions_and_unowned_files(self) -> None:
        # Unowned is a warning, never a failure (decided in #552).
        result = check_pull_request(self.manifest, ["tests/x.test.ts", "package.json", "README.md"], [])

        self.assertEqual([], result.failures)
        self.assertEqual(["README.md"], result.warnings)

    def test_labels_that_are_not_trilhas_are_ignored(self) -> None:
        result = check_pull_request(self.manifest, ["lib/b.ts"], ["onda:1", "trilha:b"])

        self.assertEqual([], result.failures)

    def test_two_trilhas_with_the_same_pattern_is_a_manifest_error(self) -> None:
        with self.assertRaises(ManifestError):
            load_manifest({"trilhas": {"trilha:a": {"": ["lib/x.ts"]}, "trilha:b": {"": ["lib/x.ts"]}}})

    def test_a_malformed_manifest_fails_instead_of_passing(self) -> None:
        for nome, raw in {
            "sem_trilhas": {},
            "rotulo_errado": {"trilhas": {"dinheiro": {"": ["lib/x.ts"]}}},
            "base_sem_barra": {"trilhas": {"trilha:a": {"lib": ["x.ts"]}}},
            "nome_vazio": {"trilhas": {"trilha:a": {"": [""]}}},
            "nome_nao_texto": {"trilhas": {"trilha:a": {"": [3]}}},
        }.items():
            with self.subTest(nome=nome):
                with self.assertRaises(ManifestError):
                    load_manifest(raw)

    def test_main_reports_failures_and_warnings_as_annotations(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            manifest = Path(folder) / "posse.json"
            manifest.write_text(json.dumps(SMALL), encoding="utf-8")
            files = Path(folder) / "files.txt"
            files.write_text("lib/b.ts\nREADME.md\n", encoding="utf-8")

            self.assertEqual(1, main(["--manifest", str(manifest), "--files", str(files), "--labels", '["trilha:a"]']))
            self.assertEqual(0, main(["--manifest", str(manifest), "--files", str(files), "--labels", '["trilha:b"]']))
            # Labels that are not a JSON list of strings cannot be checked.
            self.assertEqual(1, main(["--manifest", str(manifest), "--files", str(files), "--labels", "trilha:b"]))


class RepositoryManifestTests(unittest.TestCase):
    def test_the_repository_manifest_loads(self) -> None:
        load_manifest(json.loads(MANIFEST.read_text(encoding="utf-8")))

    def test_manifest_and_trilhas_md_list_the_same_paths(self) -> None:
        # Catches: TRILHAS.md and posse.json drifting apart (one is read by
        # people, the other by the CI).
        raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
        doc = trilhas_tokens(TRILHAS.read_text(encoding="utf-8"))

        every_name = set()
        for label, bases in raw["trilhas"].items():
            names = {name for base, items in bases.items() for name in items}
            # A base (`app/components/`) aparece no texto como contexto dos nomes.
            every_name |= names | {base for base in bases if base}
            with self.subTest(trilha=label):
                self.assertEqual(set(), names - doc.get(label, set()), "no posse.json, falta no TRILHAS.md")
        mentioned = set().union(*doc.values())
        self.assertEqual(set(), mentioned - every_name, "no TRILHAS.md, falta no posse.json")

    def test_known_files_resolve_to_their_trilha(self) -> None:
        manifest = load_manifest(json.loads(MANIFEST.read_text(encoding="utf-8")))
        expected = {
            "app/(protected)/settings/payouts/page.tsx": "trilha:dinheiro",
            "app/(protected)/settings/store/page.tsx": "trilha:membros",
            "apps/api/src/appsell_api/webhook_stripe.py": "trilha:dinheiro",
            "apps/api/src/appsell_api/pdf_sandbox.py": "trilha:midia",
            "app/components/ui/choice-select.tsx": "trilha:checkout",
            "scripts/ci/enforce_gate.py": "trilha:plataforma",
            "docs/programa/posse.json": "trilha:orquestracao",
        }
        for path, label in expected.items():
            with self.subTest(path=path):
                self.assertEqual(label, manifest.owner(path))
        self.assertIsNone(manifest.owner("docs/ci/COST_CONTAINMENT_2026-07.md"))
        self.assertTrue(manifest.is_exception("docs/ci/COST_CONTAINMENT_2026-07.md"))


if __name__ == "__main__":
    unittest.main()
