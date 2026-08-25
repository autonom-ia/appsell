import unittest

from scripts.ci.enforce_gate import evaluate_gate, expected_steps


def outcomes(**overrides: str) -> dict[str, str]:
    values = {
        "route": "success",
        "fast_web": "skipped",
        "fast_api": "skipped",
        "fast_backup": "skipped",
        "fast_preview": "skipped",
        "cert_web": "skipped",
        "cert_postgres": "skipped",
        "cert_storage": "skipped",
        "cert_openbao": "skipped",
        "cert_backup": "skipped",
        "cert_preview": "skipped",
        "image_web": "skipped",
        "image_api": "skipped",
        "image_bootstrap": "skipped",
        "smoke": "skipped",
    }
    values.update(overrides)
    return values


class ExpectedStepsTests(unittest.TestCase):
    def test_pull_request_requires_only_fast_scopes(self) -> None:
        route = {"web": True, "api": False}

        self.assertEqual({"route", "fast_web"}, expected_steps("pull_request", route))

    def test_docs_pull_request_requires_route_only(self) -> None:
        self.assertEqual(
            {"route"},
            expected_steps("pull_request", {"web": False, "api": False}),
        )

    def test_pull_request_requires_fast_backup_and_preview_when_selected(self) -> None:
        route = {"backup": True, "preview": True}

        self.assertEqual(
            {"route", "fast_backup", "fast_preview"},
            expected_steps("pull_request", route),
        )

    def test_merge_group_requires_specialized_suites_and_images(self) -> None:
        route = {
            "web": True,
            "api": True,
            "postgres": True,
            "storage": True,
            "openbao": True,
            "image_web": True,
            "image_api": True,
            "image_bootstrap": False,
            "backup": True,
            "preview": True,
        }

        self.assertEqual(
            {
                "route",
                "cert_web",
                "cert_postgres",
                "cert_storage",
                "cert_openbao",
                "cert_backup",
                "cert_preview",
                "image_web",
                "image_api",
            },
            expected_steps("merge_group", route),
        )

    def test_storage_scope_requires_the_storage_certification(self) -> None:
        route = {"storage": True}

        self.assertIn("cert_storage", expected_steps("merge_group", route))

    def test_push_keeps_conditional_certification_until_merge_queue_is_live(
        self,
    ) -> None:
        route = {
            "web": True,
            "postgres": True,
            "backup": True,
            "image_web": True,
        }

        self.assertEqual(
            {
                "route",
                "cert_web",
                "cert_postgres",
                "cert_backup",
                "image_web",
                "smoke",
            },
            expected_steps("push", route),
        )

    def test_push_becomes_smoke_only_after_explicit_queue_activation(self) -> None:
        route = {
            "web": True,
            "postgres": True,
            "image_web": True,
        }

        self.assertEqual(
            {"route", "smoke"},
            expected_steps("push", route, merge_queue_active=True),
        )


class EvaluateGateTests(unittest.TestCase):
    def test_expected_success_and_unexpected_skips_pass(self) -> None:
        failures = evaluate_gate(
            "pull_request",
            {"web": True, "api": False},
            outcomes(fast_web="success"),
        )

        self.assertEqual([], failures)

    def test_expected_skipped_fails_closed(self) -> None:
        failures = evaluate_gate(
            "pull_request", {"web": True, "api": False}, outcomes()
        )

        self.assertIn("fast_web=skipped", failures)

    def test_unknown_outcome_fails_closed(self) -> None:
        failures = evaluate_gate(
            "pull_request",
            {"web": True, "api": False},
            outcomes(fast_web="mystery"),
        )

        self.assertIn("fast_web=mystery", failures)

    def test_unexpected_execution_fails_closed(self) -> None:
        failures = evaluate_gate(
            "pull_request",
            {"web": False, "api": False},
            outcomes(cert_storage="success"),
        )

        self.assertIn("cert_storage=unexpected-success", failures)

    def test_route_failure_always_fails(self) -> None:
        failures = evaluate_gate(
            "pull_request",
            {"web": False, "api": False},
            outcomes(route="failure"),
        )

        self.assertIn("route=failure", failures)


if __name__ == "__main__":
    unittest.main()
