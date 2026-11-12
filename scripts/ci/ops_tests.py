#!/usr/bin/env python3
"""Roda a suíte de cada script do host no Python mais baixo (#591).

Os scripts de `scripts/ops/` rodam no host por cron, fora do ambiente da api,
e declaram compatibilidade com Python 3.9. A suíte inteira de
`scripts/ci/tests` não roda em 3.9 (os scripts do próprio CI usam sintaxe mais
nova), então o CI roda em 3.9 só o teste de cada script do host:
`scripts/ops/<nome>.py` tem a suíte em `scripts/ci/tests/test_<nome>.py`.

Script do host sem esse teste reprova: sem ele, a compatibilidade declarada
nunca seria provada. Cada suíte também reprova quando não tem teste nenhum,
porque o unittest do 3.9 sai com 0 nesse caso.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

OPS_DIR = Path("scripts/ops")
TESTS_DIR = Path("scripts/ci/tests")


class MissingOpsTest(Exception):
    """Um script do host não tem a suíte com o nome esperado."""


def ops_test_patterns(root: Path) -> list[str]:
    ops_dir = root / OPS_DIR
    if not ops_dir.is_dir():
        return []
    patterns: list[str] = []
    missing: list[str] = []
    for script in sorted(ops_dir.glob("*.py")):
        test_name = f"test_{script.stem}.py"
        if (root / TESTS_DIR / test_name).is_file():
            patterns.append(test_name)
        else:
            missing.append(f"{OPS_DIR / script.name} sem {TESTS_DIR / test_name}")
    if missing:
        raise MissingOpsTest("; ".join(missing))
    return sorted(patterns)


def run_pattern(tests_dir: Path, pattern: str) -> int:
    suite = unittest.TestLoader().discover(str(tests_dir), pattern=pattern)
    if suite.countTestCases() == 0:
        print(f"::error::{pattern} não tem teste nenhum", file=sys.stderr)
        return 1
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def run_all(root: Path) -> int:
    try:
        patterns = ops_test_patterns(root)
    except MissingOpsTest as error:
        print(f"::error::script do host sem teste: {error}", file=sys.stderr)
        return 1
    status = 0
    for pattern in patterns:
        status = max(status, run_pattern(root / TESTS_DIR, pattern))
    return status


def main() -> int:
    if len(sys.argv) != 1:
        print("uso: ops_tests.py (na raiz do repositório)", file=sys.stderr)
        return 2
    return run_all(Path.cwd())


if __name__ == "__main__":
    raise SystemExit(main())
