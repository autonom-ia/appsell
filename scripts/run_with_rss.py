from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a command and sample its process-tree RSS."
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    if not args.command:
        parser.error("a command is required after --")
    return args


def process_tree_rss(root_pid: int) -> int:
    result = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,rss="],
        check=True,
        capture_output=True,
        text=True,
        timeout=3,
    )
    rows = [
        tuple(map(int, line.split()))
        for line in result.stdout.splitlines()
        if line.strip()
    ]
    selected = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, parent, _rss in rows:
            if parent in selected and pid not in selected:
                selected.add(pid)
                changed = True
    return sum(rss * 1024 for pid, _parent, rss in rows if pid in selected)


def main() -> None:
    args = parse_args()
    if args.timeout <= 0:
        raise RuntimeError("timeout must be positive")
    process = subprocess.Popen(args.command, start_new_session=True)
    peak_rss = 0
    samples = 0
    timed_out = False
    deadline = time.monotonic() + args.timeout
    while process.poll() is None:
        try:
            peak_rss = max(peak_rss, process_tree_rss(process.pid))
            samples += 1
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
        if time.monotonic() >= deadline:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            break
        time.sleep(0.1)
    if timed_out:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
    return_code = process.wait()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "peak_rss_bytes": peak_rss,
                "samples": samples,
                "exit_code": 124 if timed_out else return_code,
                "timed_out": timed_out,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    raise SystemExit(124 if timed_out else return_code)


if __name__ == "__main__":
    main()
