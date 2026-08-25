import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.ci.route_changes import (
    classify_paths,
    collect_changed_paths,
    parse_name_status_z,
    route_for_event,
)


class ClassifyPathsTests(unittest.TestCase):
    def test_docs_only_emits_no_product_suite(self) -> None:
        route = classify_paths(["docs/plans/CI.md", "README.md"])

        self.assertTrue(route["docs_only"])
        self.assertFalse(route["web"])
        self.assertFalse(route["api"])
        self.assertFalse(route["full"])
        self.assertFalse(route["unknown"])

    def test_web_root_configs_cover_next_build_and_image(self) -> None:
        route = classify_paths(["proxy.ts", "tsconfig.json"])

        self.assertTrue(route["web"])
        self.assertTrue(route["image_web"])
        self.assertFalse(route["api"])

    def test_api_migration_covers_postgres_storage_and_bootstrap_image(self) -> None:
        route = classify_paths(["apps/api/migrations/versions/0021_example.py"])

        self.assertTrue(route["api"])
        self.assertTrue(route["postgres"])
        self.assertTrue(route["storage"])
        self.assertTrue(route["image_bootstrap"])

    def test_api_source_covers_bootstrap_image_real_copy_input(self) -> None:
        for path in (
            "apps/api/src/appsell_api/main.py",
            "apps/api/src/appsell_api/reset_demo_tenant.py",
            "apps/api/src/appsell_api/configure_context_key.py",
        ):
            with self.subTest(path=path):
                route = classify_paths([path])

                self.assertTrue(route["image_bootstrap"])

    def test_storage_shared_module_is_explicit(self) -> None:
        route = classify_paths(["apps/api/src/appsell_api/asset_worker_service.py"])

        self.assertTrue(route["api"])
        self.assertTrue(route["storage"])

    def test_openbao_change_covers_real_suite_and_node_inputs(self) -> None:
        route = classify_paths(
            ["lib/token-vault-provider.server.ts", "infra/openbao/policy.hcl"]
        )

        self.assertTrue(route["web"])
        self.assertTrue(route["openbao"])
        self.assertTrue(route["image_web"])

    def test_capacity_scoped_change_still_covers_storage(self) -> None:
        route = classify_paths(["lib/capacity-postgres-sampler.ts"])

        self.assertTrue(route["storage"])

    def test_compose_change_fails_safe_to_full_certification(self) -> None:
        route = classify_paths(["compose.yaml"])

        self.assertTrue(route["full"])
        self.assertFalse(route["workflow"])
        for scope in (
            "web",
            "api",
            "postgres",
            "storage",
            "openbao",
            "image_web",
            "image_api",
            "image_bootstrap",
        ):
            self.assertTrue(route[scope], scope)

    def test_unknown_file_fails_safe_to_full_certification(self) -> None:
        route = classify_paths(["new-root-control.conf"])

        self.assertTrue(route["unknown"])
        self.assertTrue(route["full"])
        self.assertFalse(route["workflow"])
        self.assertIn("new-root-control.conf", route["unknown_paths"])

    def test_pnpm_workspace_change_runs_full_product_certification(self) -> None:
        route = classify_paths(["pnpm-workspace.yaml"])

        self.assertTrue(route["full"])
        self.assertFalse(route["workflow"])
        self.assertFalse(route["unknown"])
        for scope in (
            "web",
            "api",
            "postgres",
            "storage",
            "openbao",
            "image_web",
            "image_api",
            "image_bootstrap",
        ):
            self.assertTrue(route[scope], scope)

    def test_harness_only_change_runs_workflow_lint_without_product_ci(self) -> None:
        route = classify_paths(
            [
                "platform-manifest.json",
                ".claude/rules/security.md",
                ".github/workflows/agent-harness-check.yml",
                "scripts/harness-doctor.sh",
                "scripts/validate-harness.sh",
            ]
        )

        self.assertTrue(route["workflow"])
        self.assertFalse(route["docs_only"])
        self.assertFalse(route["full"])
        self.assertFalse(route["web"])
        self.assertFalse(route["api"])

    def test_env_contract_routes_web_and_api(self) -> None:
        route = classify_paths([".env.example"])

        self.assertTrue(route["web"])
        self.assertTrue(route["api"])
        self.assertTrue(route["postgres"])
        self.assertFalse(route["full"])

    def test_postgres_script_routes_postgres_without_unknown_fallback(self) -> None:
        route = classify_paths(["scripts/test-postgres-foundation.sh"])

        self.assertTrue(route["api"])
        self.assertTrue(route["postgres"])
        self.assertFalse(route["full"])

    def test_preview_workflow_routes_preview_only(self) -> None:
        route = classify_paths([".github/workflows/preview-images.yml"])

        self.assertTrue(route["preview"])
        self.assertTrue(route["workflow"])
        self.assertFalse(route["full"])

    def test_native_image_workflow_routes_all_component_images(self) -> None:
        route = classify_paths([".github/workflows/easypanel-native-images.yml"])

        self.assertTrue(route["image_web"])
        self.assertTrue(route["image_api"])
        self.assertTrue(route["image_bootstrap"])
        self.assertTrue(route["workflow"])
        self.assertFalse(route["full"])

    def test_native_asset_worker_deploy_routes_api_image(self) -> None:
        route = classify_paths(["deploy/easypanel-native/ensure-asset-worker.sh"])

        self.assertTrue(route["image_api"])
        self.assertFalse(route["image_bootstrap"])
        self.assertFalse(route["full"])

    def test_unknown_workflow_fails_safe(self) -> None:
        route = classify_paths([".github/workflows/unclassified.yml"])

        self.assertTrue(route["unknown"])
        self.assertTrue(route["full"])
        self.assertTrue(route["workflow"])

    def test_all_playwright_configs_route_web(self) -> None:
        route = classify_paths(
            [
                "playwright.identity-real.config.ts",
                "playwright.runtime-preview.config.ts",
            ]
        )

        self.assertTrue(route["web"])
        self.assertFalse(route["full"])


class GitDiffTests(unittest.TestCase):
    def test_parser_preserves_renames_deletions_and_spaces(self) -> None:
        payload = (
            b"R100\0old name.ts\0new name.ts\0D\0deleted.py\0M\0path with spaces.ts\0"
        )

        self.assertEqual(
            ["old name.ts", "new name.ts", "deleted.py", "path with spaces.ts"],
            parse_name_status_z(payload),
        )

    def test_collect_changed_paths_uses_git_history_not_pr_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q", repo], check=True)
            subprocess.run(
                ["git", "-C", repo, "config", "user.email", "ci@example.test"],
                check=True,
            )
            subprocess.run(["git", "-C", repo, "config", "user.name", "CI"], check=True)
            (repo / "old name.ts").write_text("before\n", encoding="utf-8")
            (repo / "deleted.py").write_text("delete\n", encoding="utf-8")
            subprocess.run(["git", "-C", repo, "add", "."], check=True)
            subprocess.run(["git", "-C", repo, "commit", "-qm", "base"], check=True)
            base = subprocess.check_output(
                ["git", "-C", repo, "rev-parse", "HEAD"], text=True
            ).strip()
            subprocess.run(
                ["git", "-C", repo, "mv", "old name.ts", "new name.ts"],
                check=True,
            )
            os.remove(repo / "deleted.py")
            (repo / "path with spaces.ts").write_text("new\n", encoding="utf-8")
            subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
            subprocess.run(["git", "-C", repo, "commit", "-qm", "head"], check=True)
            head = subprocess.check_output(
                ["git", "-C", repo, "rev-parse", "HEAD"], text=True
            ).strip()

            changed = collect_changed_paths(repo, base, head, three_dot=False)

            self.assertEqual(
                {"old name.ts", "new name.ts", "deleted.py", "path with spaces.ts"},
                set(changed),
            )

    def test_invalid_sha_routes_to_full_fallback(self) -> None:
        route = route_for_event(
            event="pull_request",
            repository=Path.cwd(),
            base="0" * 40,
            head="f" * 40,
        )

        self.assertTrue(route["fallback"])
        self.assertTrue(route["full"])
        self.assertTrue(route["routing_error"])
