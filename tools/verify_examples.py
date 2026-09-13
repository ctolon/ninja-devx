"""Run example tests, types, migrations and Django checks in their own project directories."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    examples = {
        "quickstart": "config notes",
        "blog": "blog config clients/blog_client.py",
        "recipes": "config shop hacksoft cosmic interactors",
        "saas": "config tracker",
        "async_api": "config orders",
    }
    env = dict(os.environ)
    for key in (
        "DJANGO_SETTINGS_MODULE",
        "TEST_DATABASE_URL",
        "TEST_REDIS_URL",
        "TEST_S3_ENDPOINT_URL",
    ):
        env.pop(key, None)
    for name, modules in examples.items():
        print(f"Example: {name}", flush=True)
        for command in (
            ["-m", "pytest", "-q"],
            ["-m", "mypy", *modules.split()],
            ["manage.py", "makemigrations", "--check", "--dry-run"],
            ["manage.py", "check", "--fail-level", "WARNING"],
            ["manage.py", "devx_scaffold", "--check"],
        ):
            subprocess.run(
                [sys.executable, *command],
                cwd=root / "examples" / name,
                env=env,
                check=True,
                timeout=120,
            )


if __name__ == "__main__":
    main()
