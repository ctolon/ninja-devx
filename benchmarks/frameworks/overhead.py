"""Cross-framework workload characterization over the same HTTP contract.

Measures the per-request overhead each framework adds on top of Django for one
``GET /items`` endpoint. This is a characterization tool, not a ranking: read ratios
between runs on the same machine, not absolute numbers.

``python benchmarks/frameworks/overhead.py``
``python benchmarks/frameworks/overhead.py --framework ninja --requests 5000``

Available adapters depend on what is installed (Django Ninja and ninja-devx always;
Django REST framework and django-ninja-extra when importable). Django-ninja-crud is a
model-backed viewset and is intentionally not part of this HTTP-only workload.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import django
from django.conf import settings

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

WARMUP = 50


def _configure() -> None:
    settings.configure(
        DEBUG=False,
        SECRET_KEY="benchmarks-only",
        ALLOWED_HOSTS=["testserver"],
        ROOT_URLCONF="benchmarks.frameworks.urls",
        INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth", "ninja_devx"],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        USE_TZ=True,
    )


def _measure(client: object, url: str, requests: int) -> float:
    for _ in range(WARMUP):
        _ = client.get(url)  # type: ignore[attr-defined]
    start = time.perf_counter()
    for _ in range(requests):
        response = client.get(url)  # type: ignore[attr-defined]
        _ = response.content  # force body evaluation
    return (time.perf_counter() - start) / requests


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=int, default=2000, help="requests per round")
    parser.add_argument("--rounds", type=int, default=5, help="rounds; the fastest counts")
    parser.add_argument(
        "--framework",
        action="append",
        default=[],
        metavar="NAME",
        help="limit to a label (repeatable): ninja, ninja-devx, drf, ninja-extra",
    )
    args = parser.parse_args()
    if args.requests < 1 or args.rounds < 1:
        parser.error("--requests and --rounds must be positive")
    _configure()
    django.setup()

    from django.test import Client

    from benchmarks.frameworks.urls import ENDPOINTS

    targets = {
        label: url
        for label, url in ENDPOINTS.items()
        if not args.framework or label in args.framework
    }
    if not targets:
        print(f"No matching framework. Available: {', '.join(ENDPOINTS)}")
        return 1
    client = Client()
    print(f"{'framework':<14}{'us/request':>12}{'rounds':>8}{'requests':>10}")
    for label, url in targets.items():
        best = min(_measure(client, url, args.requests) for _ in range(args.rounds))
        print(f"{label:<14}{best * 1e6:>12.1f}{args.rounds:>8}{args.requests:>10}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
