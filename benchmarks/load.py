"""Load test real servers: WSGI (gunicorn) and ASGI (uvicorn), at several concurrencies.

::

    uv sync --group bench
    uv run python benchmarks/load.py                          # SQLite, 5 s per run
    BENCH_DATABASE_URL=postgresql://u:p@localhost/bench?pool=1 uv run python benchmarks/load.py
    uv run python benchmarks/load.py --servers uvicorn --concurrency 1 50 200 --duration 10

Each (server, endpoint, concurrency) reports requests per second and p50/p99 latency.
Compare a controller endpoint with its plain-Ninja twin on the same row group.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
ENDPOINTS = {
    "sync": ["/api/fn/items/1?q=x", "/api/ctl/items/1?q=x", "/api/fn/notes", "/api/ctl/notes/"],
    "async": [
        "/api/fn/aitems/1?q=x",
        "/api/ctl/aitems/1?q=x",
        "/api/fn/anotes",
        "/api/ctl/anotes/",
    ],
}


@dataclass(frozen=True, slots=True)
class Row:
    server: str
    endpoint: str
    concurrency: int
    requests_per_second: float
    p50_ms: float
    p99_ms: float
    errors: int


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def server_command(server: str, port: int, workers: int) -> list[str]:
    if server == "gunicorn":
        return [
            sys.executable, "-m", "gunicorn", "benchmarks.wsgi:application",
            "--bind", f"127.0.0.1:{port}", "--workers", str(workers),
            "--threads", "4", "--worker-class", "gthread", "--log-level", "warning",
        ]  # fmt: skip
    return [
        sys.executable, "-m", "uvicorn", "benchmarks.asgi:application",
        "--port", str(port), "--workers", str(workers), "--log-level", "warning",
        "--no-access-log",
    ]  # fmt: skip


def wait_until_up(url: str, process: subprocess.Popen[bytes], timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"server exited with {process.returncode}")
        try:
            if httpx.get(url, timeout=1).status_code < 500:
                return
        except httpx.HTTPError:
            time.sleep(0.2)
    raise RuntimeError(f"server did not answer {url}")


async def load(base: str, path: str, concurrency: int, duration: float) -> tuple[list[float], int]:
    latencies: list[float] = []
    errors = 0
    stop = time.monotonic() + duration
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(base_url=base, limits=limits, timeout=30) as client:

        async def user() -> None:
            nonlocal errors
            while time.monotonic() < stop:
                start = time.perf_counter()
                try:
                    response = await client.get(path)
                    ok = response.status_code == 200
                except httpx.HTTPError:
                    ok = False
                latencies.append(time.perf_counter() - start)
                errors += not ok

        await asyncio.gather(*(user() for _ in range(concurrency)))
    return latencies, errors


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def prepare_database(env: dict[str, str]) -> None:
    code = (
        "import django; django.setup(); from django.core.management import call_command; "
        "call_command('migrate', verbosity=0, run_syncdb=True); "
        "from benchmarks.urls import seed; seed()"
    )
    subprocess.run([sys.executable, "-c", code], env=env, cwd=ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--servers", nargs="+", default=["gunicorn", "uvicorn"])
    parser.add_argument("--concurrency", nargs="+", type=int, default=[1, 50, 200])
    parser.add_argument("--duration", type=float, default=5)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--json", type=Path)
    arguments = parser.parse_args()

    env = {**os.environ, "DJANGO_SETTINGS_MODULE": "benchmarks.settings", "PYTHONPATH": str(ROOT)}
    if "BENCH_DATABASE_URL" not in env:
        env["BENCH_SQLITE"] = str(Path(tempfile.gettempdir()) / "ninja-devx-load.sqlite3")
    prepare_database(env)

    rows: list[Row] = []
    header = ("server", "endpoint", "conc", "req/s", "p50 ms", "p99 ms", "err")
    print("{:9} {:24} {:>5} {:>9} {:>8} {:>8} {:>4}".format(*header))
    for server in arguments.servers:
        port = free_port()
        command = server_command(server, port, arguments.workers)
        process = subprocess.Popen(command, env=env, cwd=ROOT)
        try:
            base = f"http://127.0.0.1:{port}"
            wait_until_up(f"{base}/api/fn/items/1", process)
            paths = ENDPOINTS["sync" if server == "gunicorn" else "async"]
            for path in paths:
                for concurrency in arguments.concurrency:
                    asyncio.run(load(base, path, concurrency, 0.5))  # warm up
                    latencies, errors = asyncio.run(
                        load(base, path, concurrency, arguments.duration)
                    )
                    row = Row(
                        server,
                        path.removeprefix("/api"),
                        concurrency,
                        round(len(latencies) / arguments.duration, 1),
                        round(statistics.median(latencies) * 1000, 2),
                        round(percentile(latencies, 0.99) * 1000, 2),
                        errors,
                    )
                    rows.append(row)
                    print(
                        f"{row.server:9} {row.endpoint:24} {row.concurrency:5} "
                        f"{row.requests_per_second:9.1f} {row.p50_ms:8.2f} {row.p99_ms:8.2f} "
                        f"{row.errors:4}"
                    )
        finally:
            process.send_signal(signal.SIGTERM)
            process.wait(timeout=20)
    if arguments.json:
        arguments.json.write_text(json.dumps([asdict(row) for row in rows], indent=2) + "\n")
    return 1 if any(row.errors for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
