"""In-process cost of controllers compared with plain Ninja function views.

Requests go through Ninja's test clients (routing, validation, serialization) with no
server, so the numbers isolate what this package adds. Thread hops per request are
counted exactly, which makes them a stable CI gate::

    uv run python benchmarks/overhead.py                        # table
    uv run python benchmarks/overhead.py --json results.json
    uv run python benchmarks/overhead.py --check benchmarks/budget.json   # CI
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "benchmarks.settings")
os.environ.setdefault("BENCH_SQLITE", str(Path(tempfile.gettempdir()) / "ninja-devx-bench.sqlite3"))

import django

django.setup()

from django.core.management import call_command  # noqa: E402
from ninja.testing import TestAsyncClient, TestClient  # noqa: E402

from benchmarks.urls import api, seed  # noqa: E402
from ninja_devx.testing.clients import assert_max_hops  # noqa: E402


@dataclass(frozen=True, slots=True)
class Case:
    name: str
    path: str
    baseline: str | None  # the function-view case it is compared with
    asynchronous: bool = False


CASES = [
    Case("fn sync", "/fn/items/1?q=x", None),
    Case("controller sync (request scope + DI)", "/ctl/items/1?q=x", "fn sync"),
    Case("fn async", "/fn/aitems/1?q=x", None, asynchronous=True),
    Case(
        "controller async (request scope + DI)", "/ctl/aitems/1?q=x", "fn async", asynchronous=True
    ),
    Case("fn sync list (DB)", "/fn/notes", None),
    Case("controller ReadOnlyModelController list", "/ctl/notes/", "fn sync list (DB)"),
    Case("fn async list (DB)", "/fn/anotes", None, asynchronous=True),
    Case(
        "controller AsyncReadOnlyModelController list",
        "/ctl/anotes/",
        "fn async list (DB)",
        asynchronous=True,
    ),
]


@dataclass(frozen=True, slots=True)
class Result:
    name: str
    micros: float
    overhead_ratio: float | None
    hops: int


def time_sync(client: TestClient, path: str, number: int) -> float:
    start = time.perf_counter()
    for _ in range(number):
        client.get(path)
    return time.perf_counter() - start


async def time_async(client: TestAsyncClient, path: str, number: int) -> float:
    start = time.perf_counter()
    for _ in range(number):
        await client.get(path)
    return time.perf_counter() - start


async def count_hops(client: TestAsyncClient, path: str) -> int:
    assert (await client.get(path)).status_code == 200, path
    with assert_max_hops(10**9) as hops:
        await client.get(path)
    return hops.count


def run(number: int, repeat: int) -> list[Result]:
    """Cases run interleaved, ``repeat`` rounds; the fastest round counts (least noise)."""
    call_command("migrate", verbosity=0, run_syncdb=True)
    seed()
    sync_client, async_client = TestClient(api), TestAsyncClient(api)
    best: dict[str, float] = {}
    hops: dict[str, int] = {}

    loop = asyncio.new_event_loop()  # sync cases run between, not inside, loop calls
    for case in CASES:
        if case.asynchronous:
            hops[case.name] = loop.run_until_complete(count_hops(async_client, case.path))
        else:
            assert sync_client.get(case.path).status_code == 200, case.path
            hops[case.name] = 0
    for _ in range(repeat):
        for case in CASES:
            if case.asynchronous:
                elapsed = loop.run_until_complete(time_async(async_client, case.path, number))
            else:
                elapsed = time_sync(sync_client, case.path, number)
            best[case.name] = min(best.get(case.name, elapsed), elapsed)
    loop.close()
    results: list[Result] = []
    for case in CASES:
        micros = best[case.name] / number * 1e6
        ratio = best[case.name] / best[case.baseline] if case.baseline else None
        results.append(
            Result(
                case.name,
                round(micros, 1),
                None if ratio is None else round(ratio, 3),
                hops[case.name],
            )
        )
    return results


def check(results: list[Result], budget_path: Path) -> list[str]:
    budget: dict[str, dict[str, float]] = json.loads(budget_path.read_text())
    failures = []
    for result in results:
        limits = budget.get(result.name, {})
        ratio, limit = result.overhead_ratio, limits.get("max_overhead_ratio")
        if ratio is not None and limit is not None and ratio > limit:
            failures.append(f"{result.name}: {ratio:.2f}x > {limit}x")
        if "max_hops" in limits and result.hops > limits["max_hops"]:
            failures.append(f"{result.name}: {result.hops} hops > {limits['max_hops']:g}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--number", type=int, default=1000)
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--check", type=Path, metavar="BUDGET")
    arguments = parser.parse_args()
    results = run(arguments.number, arguments.repeat)
    print(f"{'case':40} {'µs/request':>11} {'vs fn':>7} {'hops':>5}")
    for result in results:
        ratio = f"{result.overhead_ratio:.2f}x" if result.overhead_ratio else ""
        print(f"{result.name:40} {result.micros:11.1f} {ratio:>7} {result.hops:5}")
    if arguments.json:
        arguments.json.write_text(json.dumps([asdict(r) for r in results], indent=2) + "\n")
    if arguments.check:
        failures = check(results, arguments.check)
        for failure in failures:
            print(f"BUDGET EXCEEDED  {failure}", file=sys.stderr)
        return 1 if failures else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
