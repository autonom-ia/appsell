#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
from pathlib import Path, PurePosixPath
from typing import Iterable


SCOPES = (
    "workflow",
    "web",
    "api",
    "postgres",
    "storage",
    "openbao",
    "image_web",
    "image_api",
    "image_bootstrap",
    "backup",
    "preview",
)

IGNORED_PREFIXES = (
    ".claude/",
    ".cursor/",
    "docs/",
    "mcp/",
    "skills/",
)
IGNORED_EXACT = {
    ".autonomia-harness.json",
    ".gitattributes",
    ".gitignore",
    "AGENTS.md",
    "CLAUDE.md",
    "CURSOR.md",
    "OPENCODE.md",
    "README.md",
    "REVIEW.md",
    "platform-manifest.json",
    "scripts/harness-doctor.sh",
    "scripts/validate-harness.sh",
}
FULL_EXACT = {
    ".github/workflows/app-ci.yml",
    "compose.yaml",
    "package.json",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
}
WEB_EXACT = {
    "eslint.config.mjs",
    "next-env.d.ts",
    "next.config.ts",
    "playwright.config.ts",
    "playwright.production.config.ts",
    "proxy.ts",
    "tsconfig.json",
    "vitest.config.ts",
}
WEB_PREFIXES = ("app/", "lib/", "tests/")
API_PREFIXES = ("apps/api/", "infra/postgres/")
OPENBAO_PREFIXES = ("infra/openbao/",)
OPENBAO_PATTERNS = (
    "lib/openbao*",
    "lib/token-vault*",
    "tests/openbao*",
    "tests/token-vault*",
    "scripts/test-openbao-transit.sh",
)
STORAGE_PATTERNS = (
    "apps/api/migrations/**",
    "apps/api/src/appsell_api/capacity_*",
    "apps/api/tests/test_capacity_*",
    "lib/capacity-*",
    "tests/*capacity*",
    "scripts/*capacity*",
    "scripts/run_with_rss.py",
    "apps/api/src/appsell_api/asset_*.py",
    "apps/api/src/appsell_api/learning_pdf.py",
    "apps/api/src/appsell_api/pdf_*.py",
    "apps/api/src/appsell_api/storage_*.py",
    "apps/api/src/appsell_api/video_verifier.py",
    "apps/api/tests/test_api_assets.py",
    "apps/api/tests/test_asset_*.py",
    "apps/api/tests/test_learning_pdf.py",
    "apps/api/tests/test_pdf_*.py",
    "apps/api/tests/test_postgres_*asset*.py",
    "apps/api/tests/test_postgres_*pdf*.py",
    "apps/api/tests/test_storage_*.py",
    "apps/api/tests/test_video_verifier.py",
    "lib/asset-*.ts",
    "lib/demo-codex-assets.json",
    "lib/file-sha256.client.ts",
    "lib/http-byte-range.ts",
    "lib/private-learning-asset.server.ts",
    "lib/storage-*.ts",
    "tests/*asset*.ts",
    "tests/*pdf*.ts",
    "tests/*storage*.ts",
    "tests/http-byte-range.test.ts",
    "scripts/generate-demo-codex-pdfs.py",
    "scripts/test-storage-vertical.sh",
    "scripts/test-storage-minio.sh",
)


def _matches(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def _is_ignored(path: str) -> bool:
    if path in IGNORED_EXACT or path.startswith(IGNORED_PREFIXES):
        return True
    if path.startswith(".github/ISSUE_TEMPLATE/"):
        return True
    if path == ".github/pull_request_template.md":
        return True
    return path.endswith(".md") and "/" not in path


def _full_route(*, fallback: bool, routing_error: bool = False) -> dict[str, object]:
    route: dict[str, object] = {scope: True for scope in SCOPES}
    route.update(
        {
            "full": True,
            "unknown": fallback,
            "unknown_paths": [],
            "docs_only": False,
            "fallback": fallback,
            "routing_error": routing_error,
            "routing_error_message": "",
            "changed_paths": [],
            "changed_count": 0,
        }
    )
    return route


def classify_paths(paths: Iterable[str]) -> dict[str, object]:
    normalized: list[str] = []
    for raw in paths:
        path = raw.replace("\\", "/").removeprefix("./")
        pure = PurePosixPath(path)
        if not path or pure.is_absolute() or ".." in pure.parts:
            path = raw
        if path not in normalized:
            normalized.append(path)

    route: dict[str, object] = {scope: False for scope in SCOPES}
    unknown_paths: list[str] = []
    full = False

    for path in normalized:
        if path in FULL_EXACT or path.startswith("scripts/ci/"):
            full = True
            continue
        if path == ".github/workflows/agent-harness-check.yml":
            route["workflow"] = True
            continue
        if _is_ignored(path):
            continue

        matched = False

        if path == ".env.example":
            route["web"] = True
            route["api"] = True
            route["postgres"] = True
            matched = True

        if fnmatch.fnmatchcase(path, "playwright*.config.ts"):
            route["web"] = True
            route["image_web"] = True
            matched = True

        if path == ".github/workflows/preview-images.yml":
            route["workflow"] = True
            route["preview"] = True
            matched = True
        elif path == ".github/workflows/easypanel-native-images.yml":
            route["workflow"] = True
            route["image_web"] = True
            route["image_api"] = True
            route["image_bootstrap"] = True
            route["backup"] = True
            matched = True

        if path == "scripts/test-postgres-foundation.sh":
            route["api"] = True
            route["postgres"] = True
            matched = True
        elif path == "scripts/with-isolated-redis.mjs":
            route["web"] = True
            matched = True
        elif path in {
            "scripts/run-real-domain-api-local.sh",
            "scripts/run-real-identity-cross-repo.mjs",
        }:
            route["web"] = True
            matched = True
        elif path == "scripts/start-local-demo-preview.sh":
            route["web"] = True
            route["preview"] = True
            matched = True

        if path in WEB_EXACT or path.startswith(WEB_PREFIXES):
            route["web"] = True
            route["image_web"] = True
            matched = True

        if path.startswith(API_PREFIXES):
            route["api"] = True
            route["postgres"] = True
            route["image_api"] = True
            matched = True
            if (
                path.startswith("apps/api/src/")
                or path.startswith("apps/api/migrations/")
                or path == "apps/api/alembic.ini"
            ):
                route["image_bootstrap"] = True

        if path.startswith(OPENBAO_PREFIXES) or _matches(path, OPENBAO_PATTERNS):
            route["openbao"] = True
            matched = True

        if _matches(path, STORAGE_PATTERNS) or path.startswith(
            ("demo/", "output/pdf/")
        ):
            route["storage"] = True
            route["api"] = True
            route["postgres"] = True
            route["image_api"] = True
            if path.startswith(("demo/", "output/pdf/")):
                route["image_web"] = True
                route["image_bootstrap"] = True
            matched = True

        if path == ".dockerignore":
            route["image_web"] = True
            route["image_api"] = True
            route["image_bootstrap"] = True
            matched = True
        elif path.startswith("deploy/easypanel-native/backup/"):
            route["backup"] = True
            matched = True
        elif path.startswith("deploy/easypanel-native/"):
            name = PurePosixPath(path).name.lower()
            if "web" in name:
                route["image_web"] = True
            elif "api" in name or "asset-worker" in name:
                route["image_api"] = True
            else:
                route["image_bootstrap"] = True
            matched = True
        elif path.startswith(("deploy/easypanel/", "deploy/preview-vps/")):
            route["preview"] = True
            matched = True

        if path in {
            "apps/api/pyproject.toml",
            "apps/api/uv.lock",
            "apps/api/alembic.ini",
        }:
            route["image_api"] = True
            route["image_bootstrap"] = True
            matched = True

        if not matched:
            unknown_paths.append(path)

    if full or unknown_paths:
        safe = _full_route(fallback=bool(unknown_paths))
        safe["workflow"] = bool(route["workflow"]) or any(
            path.startswith(".github/workflows/") for path in normalized
        )
        safe["unknown_paths"] = unknown_paths
        safe["changed_paths"] = normalized
        safe["changed_count"] = len(normalized)
        return safe

    product_selected = any(bool(route[scope]) for scope in SCOPES)
    route.update(
        {
            "full": False,
            "unknown": False,
            "unknown_paths": [],
            "docs_only": bool(normalized) and not product_selected,
            "fallback": False,
            "routing_error": False,
            "routing_error_message": "",
            "changed_paths": normalized,
            "changed_count": len(normalized),
        }
    )
    return route


def parse_name_status_z(payload: bytes) -> list[str]:
    fields = payload.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    paths: list[str] = []
    index = 0
    while index < len(fields):
        status = fields[index].decode("ascii", errors="strict")
        index += 1
        path_count = 2 if status.startswith(("R", "C")) else 1
        if index + path_count > len(fields):
            raise ValueError(f"truncated git diff record for status {status!r}")
        for _ in range(path_count):
            paths.append(fields[index].decode("utf-8", errors="surrogateescape"))
            index += 1
    return paths


def collect_changed_paths(
    repository: Path, base: str, head: str, *, three_dot: bool
) -> list[str]:
    for revision in (base, head):
        subprocess.run(
            ["git", "-C", str(repository), "cat-file", "-e", f"{revision}^{{commit}}"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    separator = "..." if three_dot else ".."
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "diff",
            "--name-status",
            "-z",
            "--find-renames",
            f"{base}{separator}{head}",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return parse_name_status_z(result.stdout)


def route_for_event(
    *,
    event: str,
    repository: Path,
    base: str | None = None,
    head: str | None = None,
) -> dict[str, object]:
    if event == "workflow_dispatch":
        route = _full_route(fallback=False)
        route["event"] = event
        return route
    if event not in {"pull_request", "merge_group", "push"}:
        route = _full_route(fallback=True, routing_error=True)
        route["event"] = event
        route["routing_error_message"] = f"unsupported event: {event}"
        return route
    if not base or not head or base == "0" * 40:
        route = _full_route(fallback=True, routing_error=True)
        route["event"] = event
        route["routing_error_message"] = "base/head revision unavailable"
        return route

    try:
        paths = collect_changed_paths(
            repository, base, head, three_dot=event == "pull_request"
        )
    except (OSError, subprocess.CalledProcessError, UnicodeError, ValueError) as error:
        route = _full_route(fallback=True, routing_error=True)
        route["event"] = event
        route["routing_error_message"] = type(error).__name__
        return route

    route = classify_paths(paths)
    route["event"] = event
    return route


def write_github_output(route: dict[str, object], output_path: Path) -> None:
    with output_path.open("a", encoding="utf-8") as output:
        for key in (
            *SCOPES,
            "full",
            "unknown",
            "docs_only",
            "fallback",
            "routing_error",
        ):
            output.write(f"{key}={str(bool(route[key])).lower()}\n")
        output.write(f"changed_count={int(route['changed_count'])}\n")
        output.write(
            "route_json="
            + json.dumps(route, separators=(",", ":"), sort_keys=True)
            + "\n"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()

    route = route_for_event(
        event=args.event,
        repository=args.repository,
        base=args.base,
        head=args.head,
    )
    if args.github_output:
        write_github_output(route, args.github_output)
    print(json.dumps(route, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
