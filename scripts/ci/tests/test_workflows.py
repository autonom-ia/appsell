import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def workflow(name: str) -> str:
    return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


class AppCiWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = workflow("app-ci.yml")

    def test_required_gate_always_emits_from_one_runner(self) -> None:
        self.assertIn("  ci-gate:", self.source)
        self.assertEqual(1, len(re.findall(r"^    runs-on:", self.source, re.M)))
        self.assertNotIn("    paths:", self.source)

    def test_events_cover_fast_final_scheduled_and_smoke_paths(self) -> None:
        for event in (
            "pull_request:",
            "merge_group:",
            "push:",
            "schedule:",
            "workflow_dispatch:",
        ):
            self.assertIn(event, self.source)
        self.assertIn("types: [checks_requested]", self.source)
        self.assertIn("branches: [main]", self.source)

    def test_only_pull_request_runs_are_cancelled(self) -> None:
        self.assertIn("concurrency:", self.source)
        self.assertIn(
            "cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
            self.source,
        )

    def test_push_main_retains_certification_during_queue_transition(self) -> None:
        self.assertIn(
            "github.event_name == 'push' && steps.policy.outputs.product_checks == 'true'",
            self.source,
        )
        self.assertIn("github.event_name == 'push'", self.source)
        self.assertIn("APPSELL_MERGE_QUEUE_ACTIVE", self.source)
        self.assertIn("CI_MERGE_QUEUE_ACTIVE", self.source)
        self.assertIn("id: policy", self.source)

    def test_router_and_gate_contract_are_wired(self) -> None:
        for step_id in (
            "route",
            "fast-web",
            "fast-api",
            "fast-backup",
            "fast-preview",
            "cert-web",
            "cert-postgres",
            "cert-storage",
            "cert-openbao",
            "cert-capacity",
            "cert-backup",
            "cert-preview",
            "image-web",
            "image-api",
            "image-bootstrap",
            "smoke",
        ):
            self.assertIn(f"id: {step_id}", self.source)
        self.assertIn("python3 scripts/ci/route_changes.py", self.source)
        self.assertIn("python3 scripts/ci/enforce_gate.py", self.source)
        self.assertIn(
            "CI_ROUTE_JSON: ${{ steps.route.outputs.route_json }}", self.source
        )
        self.assertIn(
            "CI_OUTCOME_CERT_CAPACITY: ${{ steps.cert-capacity.outcome }}", self.source
        )
        self.assertIn(
            "CI_OUTCOME_CERT_BACKUP: ${{ steps.cert-backup.outcome }}", self.source
        )
        self.assertIn(
            "CI_OUTCOME_CERT_PREVIEW: ${{ steps.cert-preview.outcome }}", self.source
        )

    def test_diff_uses_event_specific_shas_and_full_history(self) -> None:
        self.assertIn("fetch-depth: 0", self.source)
        self.assertIn("github.event.pull_request.base.sha", self.source)
        self.assertIn("github.event.pull_request.head.sha", self.source)
        self.assertIn("github.event.merge_group.base_sha", self.source)
        self.assertIn("github.event.merge_group.head_sha", self.source)
        self.assertIn("github.event.before", self.source)

    def test_all_external_actions_are_sha_pinned(self) -> None:
        action_lines = re.findall(r"^\s*(?:- )?uses: (.+)$", self.source, re.M)
        self.assertTrue(action_lines)
        for action in action_lines:
            reference = action.split("#", 1)[0].strip()
            self.assertRegex(reference, r"@[0-9a-f]{40}$", action)

    def test_pnpm_exists_before_setup_node_cache_is_requested(self) -> None:
        pnpm_setup = self.source.index("uses: pnpm/action-setup@")
        node_setup = self.source.index("uses: actions/setup-node@")

        self.assertLess(pnpm_setup, node_setup)
        self.assertIn("version: 10.20.0", self.source[pnpm_setup:node_setup])

    def test_workflow_changes_run_checksum_pinned_actionlint(self) -> None:
        script = (ROOT / "scripts" / "ci" / "run_actionlint.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("bash scripts/ci/run_actionlint.sh", self.source)
        self.assertIn("find .github/workflows", self.source)
        self.assertIn("agent-harness-check.yml", self.source)
        self.assertIn('"${workflows[@]}"', self.source)
        self.assertIn(
            "bash scripts/ci/run_actionlint.sh -shellcheck= .github/workflows/agent-harness-check.yml",
            self.source,
        )
        self.assertIn("ACTIONLINT_VERSION=1.7.12", script)
        self.assertIn(
            "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8",
            script,
        )
        self.assertIn("sha256sum --check --status", script)
        self.assertIn('if [ "$os" = linux ]; then', script)
        self.assertIn("steps.route.outputs.workflow == 'true'", self.source)

    def test_postgres_artifact_upload_requires_successful_evidence_scan(self) -> None:
        self.assertIn("id: postgres-evidence-scan", self.source)
        self.assertIn("steps.postgres-evidence-scan.outcome == 'success'", self.source)
        self.assertNotIn(
            "steps.cert-postgres.outcome == 'success' }}\n        uses: actions/upload-artifact@",
            self.source,
        )

    def test_preview_contract_is_exercised_for_manual_provenance(self) -> None:
        self.assertIn(
            "APPSELL_ARTIFACT_EVENT=workflow_dispatch bash deploy/preview-vps/test-verify-downloads-local.sh",
            self.source,
        )


class ExpensiveWorkflowTriggerTests(unittest.TestCase):
    def test_native_images_are_manual_or_scheduled_only(self) -> None:
        source = workflow("easypanel-native-images.yml")

        self.assertIn("workflow_dispatch:", source)
        self.assertIn("schedule:", source)
        self.assertNotIn("pull_request:", source)
        self.assertNotRegex(source, r"(?m)^  push:")
        self.assertIn(
            "group: easypanel-native-rehearsal-${{ github.event_name }}-${{ github.ref }}",
            source,
        )
        self.assertIn("cancel-in-progress: false", source)

    def test_legacy_preview_is_manual_only(self) -> None:
        source = workflow("preview-images.yml")

        self.assertIn("workflow_dispatch:", source)
        self.assertNotIn("pull_request:", source)
        self.assertNotRegex(source, r"(?m)^  push:")
        self.assertIn("cancel-in-progress: false", source)
        self.assertIn('test "$RUNTIME_PREVIEW_ISSUE" = "$expected_issue"', source)
        self.assertIn("APPSELL_ARTIFACT_EVENT=workflow_dispatch", source)

    def test_preview_runbooks_select_manual_artifact_event(self) -> None:
        for relative in (
            "deploy/easypanel/README.md",
            "deploy/preview-vps/README.md",
        ):
            with self.subTest(relative=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("APPSELL_ARTIFACT_EVENT=workflow_dispatch", source)


if __name__ == "__main__":
    unittest.main()
