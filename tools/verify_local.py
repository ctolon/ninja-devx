"""Run bounded local tests with branch coverage; never edits CI or Git configuration.

uv run --with coverage python tools/verify_local.py --output /tmp/devx-validation
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
from pathlib import Path


def run(command: list[str], *, root: Path, timeout: int, env: dict[str, str]) -> int:
    process = subprocess.Popen(command, cwd=root, env=env, start_new_session=True)
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        print(f"Validation exceeded {timeout}s", file=sys.stderr)
        return 124


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--fail-under", type=float, default=0)
    parser.add_argument("--critical-under", type=float, default=80)
    parser.add_argument("tests", nargs="*", default=["tests"])
    args = parser.parse_args()
    if args.timeout < 1 or not all(0 <= x <= 100 for x in (args.fail_under, args.critical_under)):
        parser.error("timeout must be positive and fail-under must be between 0 and 100")
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "COVERAGE_FILE": str(output / ".coverage")}
    command = [sys.executable, "-m", "coverage"]
    status = run(
        [*command, "run", "--branch", "--source=ninja_devx", "-m", "pytest", *args.tests, "-q"],
        root=root,
        timeout=args.timeout,
        env=env,
    )
    if status:
        return status
    for options in [
        ["json", "-o", str(output / "coverage.json")],
        ["html", "-d", str(output / "html")],
        ["report", "--skip-covered", f"--fail-under={args.fail_under}"],
    ]:
        status = run([*command, *options], root=root, timeout=args.timeout, env=env)
        if status:
            return status
    report = json.loads((output / "coverage.json").read_text())
    critical = (
        "security/permission_leaf.py",
        "_permission_eval.py",
        "_permission_eval_async.py",
        "dependencies/engine.py",
        "idempotency/policy.py",
        "idempotency/store.py",
        "crud/writes.py",
        "contrib/webhooks/outbox.py",
        "_internal/streaming.py",
    )
    failures = []
    for suffix in critical:
        matches = [v for k, v in report["files"].items() if k.endswith("/" + suffix)]
        if not matches or matches[0]["summary"]["percent_covered"] < args.critical_under:
            failures.append(suffix)
    if failures:
        print(f"Critical coverage below {args.critical_under}%: {', '.join(failures)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
