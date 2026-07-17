#!/usr/bin/env python3
"""Reject sensitive material before capacity evidence is uploaded."""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path

FORBIDDEN = {
    "database_url": re.compile(r"postgres(?:ql)?(?:\+[a-z0-9_]+)?://[^\s\"']+", re.I),
    "authorization": re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{8,}", re.I),
    "cookie": re.compile(r"\b(?:Cookie\s*:|appsell_(?:session|oauth_tx)=)", re.I),
    "oauth_parameter": re.compile(
        r"(?:[?&]|\b)(?:code|state|access_token|refresh_token)=", re.I
    ),
    "email": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}
SENSITIVE_ENV = re.compile(
    r"(?:PASSWORD|SECRET|TOKEN|CREDENTIAL|PRIVATE_KEY|DATABASE_URL|(?:^|_)(?:KEY|KEYRING|HMAC|ATTESTATION)(?:_|$))",
    re.I,
)


def findings(path: Path) -> list[str]:
    content = path.read_text(encoding="utf-8", errors="replace")
    result = [code for code, pattern in FORBIDDEN.items() if pattern.search(content)]
    for name, value in os.environ.items():
        if len(value) >= 8 and SENSITIVE_ENV.search(name) and value in content:
            result.append("sensitive_environment_value")
            break
    return sorted(set(result))


def self_test() -> None:
    canaries = {
        "database_url": "postgresql://user:sentinel@127.0.0.1/appsell",
        "authorization": "Bearer sentinel-token-value",
        "cookie": "Cookie: appsell_session=sentinel-cookie",
        "oauth_parameter": "https://example.test/callback?code=sentinel&state=sentinel",
        "email": "buyer-canary@autonomia.site",
        "private_key": "-----BEGIN PRIVATE KEY-----",
    }
    with tempfile.TemporaryDirectory(prefix="appsell-capacity-scan-") as directory:
        path = Path(directory) / "artifact.json"
        for expected, canary in canaries.items():
            path.write_text(canary, encoding="utf-8")
            if expected not in findings(path):
                raise RuntimeError(f"capacity artifact scanner missed {expected}")
        path.write_text('{"status":"passed","failure":null}\n', encoding="utf-8")
        if findings(path):
            raise RuntimeError(
                "capacity artifact scanner rejected allowlisted evidence"
            )
        sensitive_names = (
            "APPSELL_CONTEXT_SIGNING_KEY_HEX",
            "APPSELL_BOOTSTRAP_ATTESTATION_KEY_HEX",
            "APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON",
            "APPSELL_STORAGE_CONTROL_ACCESS_KEY",
        )
        for index, name in enumerate(sensitive_names):
            value = f"capacity-sensitive-canary-{index}"
            previous = os.environ.get(name)
            try:
                os.environ[name] = value
                path.write_text(value, encoding="utf-8")
                if "sensitive_environment_value" not in findings(path):
                    raise RuntimeError(
                        f"capacity artifact scanner missed sensitive env class {index}"
                    )
            finally:
                if previous is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = previous


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--self-test", action="store_true")
    arguments = parser.parse_args()
    if arguments.self_test:
        self_test()
    failed = False
    for path in arguments.paths:
        if not path.is_file():
            print(f"capacity artifact missing: {path}", file=sys.stderr)
            failed = True
            continue
        codes = findings(path)
        if codes:
            print(
                f"capacity artifact rejected: {path}; categories={','.join(codes)}; values redacted",
                file=sys.stderr,
            )
            failed = True
    if failed:
        return 1
    print("capacity artifact scan: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
