"""Build a wheel, docs and focused tests from an extracted source distribution."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="devx-sdist-") as directory:
        with tarfile.open(args.archive.resolve()) as archive:
            archive.extractall(directory, filter="data")
        roots = list(Path(directory).iterdir())
        if len(roots) != 1 or not roots[0].is_dir():
            parser.error("sdist must have one root directory")
        source = roots[0]
        env = {**os.environ, "PYTHONPATH": str(source / "src") + os.pathsep + str(source)}
        env.pop("TEST_DATABASE_URL", None)
        env.pop("DJANGO_SETTINGS_MODULE", None)
        commands = [
            ["uv", "build", "--wheel", "--out-dir", str(Path(directory) / "wheel")],
            [sys.executable, "tools/generate_docs.py", "--check"],
            [sys.executable, "tools/messages.py", "--check"],
            [sys.executable, "-m", "mkdocs", "build", "--strict"],
            [sys.executable, "tools/check_docs_links.py", "site"],
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/test_i18n.py",
                "tests/test_generated_docs.py",
                "tests/test_scaffold.py",
                "-q",
            ],
        ]
        for command in commands:
            subprocess.run(command, cwd=source, env=env, check=True, timeout=180)


if __name__ == "__main__":
    main()
