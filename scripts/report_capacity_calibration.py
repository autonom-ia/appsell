#!/usr/bin/env python3
"""Render one capacity run and certify exactly three eligible same-SHA artifacts."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any


def load_report(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"capacity report is not an object: {path}")
    for field, expected in (
        ("status", str),
        ("calibration_eligible", bool),
        ("calibration_sha", str),
        ("calibration_expected_sha", str),
        ("calibration_run_identity", str),
        ("warnings", list),
        ("git_sha", str),
        ("source_evidence", dict),
    ):
        if not isinstance(value.get(field), expected):
            raise ValueError(f"capacity report has invalid {field}: {path}")
    if not value["calibration_sha"] or not value["calibration_run_identity"]:
        raise ValueError(f"capacity report identity is empty: {path}")
    if any(not isinstance(warning, str) for warning in value["warnings"]):
        raise ValueError(f"capacity report warnings are invalid: {path}")
    calibration_sha = value["calibration_sha"]
    source_sha = value["source_evidence"].get("git_sha")
    if re.fullmatch(r"[0-9a-f]{40}", calibration_sha) is None:
        raise ValueError(f"capacity report calibration SHA is not canonical: {path}")
    if not isinstance(source_sha, str) or source_sha != calibration_sha:
        raise ValueError(
            f"capacity report source SHA does not match calibration SHA: {path}"
        )
    if value["git_sha"] != calibration_sha:
        raise ValueError(
            f"capacity report git SHA does not match calibration SHA: {path}"
        )
    if value["calibration_expected_sha"] != calibration_sha:
        raise ValueError(
            f"capacity report expected SHA does not match checkout SHA: {path}"
        )
    return value


def render(report: dict[str, Any]) -> str:
    eligible = report["calibration_eligible"] is True
    return "\n".join(
        (
            "## Capacity calibration",
            "",
            f"- Status: `{report['status']}`",
            f"- Eligible: `{'yes' if eligible else 'no'}`",
            f"- SHA: `{report['calibration_sha']}`",
            f"- Run: `{report['calibration_run_identity']}`",
            f"- Warnings: `{json.dumps(report['warnings'], separators=(',', ':'))}`",
            "",
        )
    )


def report_one(path: Path) -> None:
    report = load_report(path)
    summary = render(report)
    print(summary)
    github_summary = os.getenv("GITHUB_STEP_SUMMARY")
    if github_summary:
        with Path(github_summary).open("a", encoding="utf-8") as handle:
            handle.write(summary)
    if report["calibration_eligible"] is not True:
        print(
            "::warning title=Capacity run not calibration eligible::"
            "Artifact must not count toward the three-run certificate"
        )


def certify_three(paths: list[Path]) -> None:
    if len(paths) != 3:
        raise ValueError("capacity calibration requires exactly three artifacts")
    reports = [load_report(path) for path in paths]
    if any(
        report["status"] != "passed"
        or report["calibration_eligible"] is not True
        or report["warnings"]
        for report in reports
    ):
        raise ValueError(
            "all three capacity artifacts must be passed and eligible without warnings"
        )
    shas = {report["calibration_sha"] for report in reports}
    if len(shas) != 1:
        raise ValueError("capacity artifacts must use the same head SHA")
    identities = {report["calibration_run_identity"] for report in reports}
    if len(identities) != 3:
        raise ValueError("capacity artifacts must come from three distinct runs")
    print(f"capacity calibration certificate: passed; sha={next(iter(shas))}; runs=3")


def self_test() -> None:
    with tempfile.TemporaryDirectory(
        prefix="appsell-capacity-calibration-"
    ) as directory:
        root = Path(directory)
        paths: list[Path] = []
        for index in range(3):
            path = root / f"run-{index}.json"
            path.write_text(
                json.dumps(
                    {
                        "status": "passed",
                        "calibration_eligible": True,
                        "calibration_sha": "a" * 40,
                        "calibration_expected_sha": "a" * 40,
                        "calibration_run_identity": f"run-{index}",
                        "warnings": [],
                        "git_sha": "a" * 40,
                        "source_evidence": {"git_sha": "a" * 40},
                    }
                ),
                encoding="utf-8",
            )
            paths.append(path)
        certify_three(paths)
        rejected = json.loads(paths[0].read_text(encoding="utf-8"))
        rejected["calibration_eligible"] = False
        rejected["warnings"] = ["isolated_full_api_pool_wait_requires_same_sha_rerun"]
        paths[0].write_text(json.dumps(rejected), encoding="utf-8")
        try:
            certify_three(paths)
        except ValueError:
            pass
        else:
            raise RuntimeError("calibration self-test accepted an ineligible artifact")
        accepted = json.loads(paths[0].read_text(encoding="utf-8"))
        accepted["calibration_eligible"] = True
        accepted["warnings"] = []
        accepted["calibration_sha"] = "not-a-git-sha"
        paths[0].write_text(json.dumps(accepted), encoding="utf-8")
        try:
            certify_three(paths)
        except ValueError:
            pass
        else:
            raise RuntimeError("calibration self-test accepted a non-canonical SHA")
        accepted["calibration_sha"] = "a" * 40
        accepted["source_evidence"]["git_sha"] = "b" * 40
        paths[0].write_text(json.dumps(accepted), encoding="utf-8")
        try:
            certify_three(paths)
        except ValueError:
            pass
        else:
            raise RuntimeError("calibration self-test accepted a source SHA mismatch")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path)
    parser.add_argument("--certify-three", nargs="*", type=Path)
    parser.add_argument("--self-test", action="store_true")
    arguments = parser.parse_args()
    try:
        if arguments.self_test:
            self_test()
        if arguments.report:
            report_one(arguments.report)
        if arguments.certify_three is not None:
            certify_three(arguments.certify_three)
        if not any(
            (arguments.self_test, arguments.report, arguments.certify_three is not None)
        ):
            parser.error("choose --report, --certify-three, or --self-test")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"capacity calibration rejected: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
