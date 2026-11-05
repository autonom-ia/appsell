import json
import tempfile
import unittest
from pathlib import Path

from scripts.ci.check_ownership import (
    ManifestError,
    check_pull_request,
    escape_message,
    escape_property,
    load_manifest,
    main,
    trilhas_tokens,
)

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "docs" / "programa" / "posse.json"
TRILHAS = ROOT / "docs" / "programa" / "TRILHAS.md"
# Caminhos que a linha de uma trilha cita mas que são de outra: a passagem
# futura da trilha 2 para a 5 e a exceção de `settings/` da trilha 3.
CROSS_MENTIONS = {
    "trilha:checkout": {
        "app/api/checkout/",
        "app/(protected)/checkout/",
        "checkout-workspace.tsx",
        "stripe-checkout-card.tsx",
        "lib/payments/",
    },
    "trilha:membros": {"app/(protected)/settings/payouts/"},
}

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
            files = Path(folder) / "files.bin"
            files.write_bytes(b"lib/b.ts\0README.md\0")

            self.assertEqual(1, main(["--manifest", str(manifest), "--files", str(files), "--labels", '["trilha:a"]']))
            self.assertEqual(0, main(["--manifest", str(manifest), "--files", str(files), "--labels", '["trilha:b"]']))
            # Labels that are not a JSON list of strings cannot be checked.
            self.assertEqual(1, main(["--manifest", str(manifest), "--files", str(files), "--labels", "trilha:b"]))

    def test_non_ascii_paths_read_from_nul_separated_git_output(self) -> None:
        # Catches: git quoting "caf\303\251.ts" and the owned file passing as
        # an unowned warning.
        with tempfile.TemporaryDirectory() as folder:
            manifest = Path(folder) / "posse.json"
            manifest.write_text(json.dumps(SMALL), encoding="utf-8")
            files = Path(folder) / "files.bin"
            files.write_bytes("app/a/b/café.ts".encode() + b"\0" + b"README.md\0")

            self.assertEqual(1, main(["--manifest", str(manifest), "--files", str(files), "--labels", '["trilha:a"]']))
            files.write_bytes(b"\xff\xfe\0")
            self.assertEqual(1, main(["--manifest", str(manifest), "--files", str(files), "--labels", '["trilha:b"]']))

    def test_case_differences_do_not_escape_the_owner(self) -> None:
        result = check_pull_request(self.manifest, ["App/A/B/x.tsx", "LIB/B.ts"], ["trilha:a"])

        self.assertEqual(["trilha:b: App/A/B/x.tsx", "trilha:b: LIB/B.ts"], result.failures)

    def test_labels_compare_without_case_or_spaces(self) -> None:
        result = check_pull_request(self.manifest, ["lib/b.ts"], [" Trilha:B "])

        self.assertEqual([], result.failures)

    def test_a_glob_does_not_cross_folders(self) -> None:
        # lib/a-* is lib/a-<file>, not lib/a-x/<anything>.
        result = check_pull_request(self.manifest, ["lib/a-x/deep.ts"], [])

        self.assertEqual([], result.failures)
        self.assertEqual(["lib/a-x/deep.ts"], result.warnings)

    def test_annotations_escape_what_the_runner_would_parse(self) -> None:
        self.assertEqual("a%25b%0Ac%3Ad%2Ce", escape_property("a%b\nc:d,e"))
        self.assertEqual("a%25b%0Dc%0A", escape_message("a%b\rc\n"))

    def test_exception_bases_and_table_rows_without_label(self) -> None:
        manifest = load_manifest({"trilhas": {"trilha:a": {"": ["x.ts"]}}, "excecoes": {"outra/": ["y"]}})
        self.assertTrue(manifest.is_exception("outra/y"))
        with self.assertRaises(ManifestError):
            load_manifest({"trilhas": {"trilha:a": {"": ["x.ts"]}}, "excecoes": {"outra": ["y"]}})
        with self.assertRaises(ManifestError):
            trilhas_tokens("| Sem rótulo | `lib/x.ts` |\n\n## Outra seção\n| `0062` | livre |\n")
        with self.assertRaises(ManifestError):
            trilhas_tokens("| Dois | `trilha:a` `trilha:b` | `lib/x.ts` |\n")


class RepositoryManifestTests(unittest.TestCase):
    def test_the_repository_manifest_loads(self) -> None:
        load_manifest(json.loads(MANIFEST.read_text(encoding="utf-8")))

    def test_manifest_and_trilhas_md_list_the_same_paths_per_trilha(self) -> None:
        # Catches: TRILHAS.md and posse.json drifting apart, in either
        # direction, trilha by trilha.
        raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
        doc = trilhas_tokens(TRILHAS.read_text(encoding="utf-8"))

        self.assertEqual(set(raw["trilhas"]), set(doc))
        for label, bases in raw["trilhas"].items():
            names = {name for items in bases.values() for name in items}
            context = {base for base in bases if base}
            mentions = CROSS_MENTIONS.get(label, set())
            with self.subTest(trilha=label):
                self.assertEqual(set(), names - doc[label], "no posse.json, falta no TRILHAS.md")
                self.assertEqual(set(), doc[label] - names - context - mentions, "no TRILHAS.md, falta no posse.json")
        for label, mentioned in CROSS_MENTIONS.items():
            with self.subTest(citacao=label):
                owned_elsewhere = {
                    name
                    for other, bases in raw["trilhas"].items()
                    if other != label
                    for items in bases.values()
                    for name in items
                }
                self.assertEqual(set(), mentioned - owned_elsewhere)

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
