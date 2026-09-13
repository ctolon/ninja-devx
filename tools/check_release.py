"""Validate a release tag and extract the reviewed changelog section without publishing."""

from __future__ import annotations

import argparse
import re
import tomllib
from pathlib import Path


def release_notes(root: Path, ref: str) -> str:
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    if ref != f"refs/tags/v{version}":
        raise ValueError(f"Expected refs/tags/v{version}, received {ref!r}")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?", version):
        raise ValueError("Release version must be an explicit three-part version")
    changelog = (root / "CHANGELOG.md").read_text()
    matches = list(re.finditer(rf"^## {re.escape(version)}(?: — [^\n]+)?$", changelog, re.M))
    if len(matches) != 1:
        raise ValueError(f"Changelog must contain exactly one '## {version}' release section")
    section = changelog[matches[0].end() :].split("\n## ", 1)[0].strip()
    if not section or "TODO" in section or "TBD" in section:
        raise ValueError("Release notes are empty or contain unresolved placeholders")
    return section + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ref")
    parser.add_argument("--notes", type=Path)
    args = parser.parse_args()
    try:
        notes = release_notes(Path(__file__).resolve().parents[1], args.ref)
    except ValueError as exc:
        parser.exit(1, f"Release validation failed: {exc}\n")
    if args.notes:
        args.notes.write_text(notes)
    print("Release tag, package version and changelog agree")


if __name__ == "__main__":
    main()
