"""Benchmark settings: the test app, a file SQLite database or ``BENCH_DATABASE_URL``."""

import os
from pathlib import Path
from urllib.parse import urlparse

from tests.settings import *  # noqa: F403

ROOT_URLCONF = "benchmarks.urls"
DEBUG = False
ALLOWED_HOSTS = ["*"]
LOGGING = {"version": 1, "disable_existing_loggers": True}

_url = os.environ.get("BENCH_DATABASE_URL")
if _url:  # postgresql://user:password@host:5432/name[?pool=1]
    parsed = urlparse(_url)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": parsed.path.lstrip("/"),
            "USER": parsed.username or "",
            "PASSWORD": parsed.password or "",
            "HOST": parsed.hostname or "",
            "PORT": str(parsed.port or 5432),
            "OPTIONS": {"pool": True} if "pool=1" in parsed.query else {},
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.environ.get("BENCH_SQLITE", str(Path(__file__).parent / "bench.sqlite3")),
        }
    }
