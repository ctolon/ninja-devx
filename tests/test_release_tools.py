"""Publication guards reject wrong refs and incomplete release notes."""

import re
from pathlib import Path

import pytest

import ninja_devx
from tools.check_release import release_notes


@pytest.fixture
def release_tree(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.0.1"\n')
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## 0.0.1\n\nInitial alpha.\n")
    return tmp_path


@pytest.mark.parametrize("ref", ["refs/heads/main", "refs/tags/v0.0.2", "refs/tags/v0.0.1-extra"])
def test_release_rejects_nonmatching_ref(release_tree, ref):
    with pytest.raises(ValueError, match="Expected"):
        release_notes(release_tree, ref)


def test_release_extracts_only_reviewed_section(release_tree):
    assert release_notes(release_tree, "refs/tags/v0.0.1") == "Initial alpha.\n"


@pytest.mark.parametrize("body", ["", "TODO finish notes", "TBD", "Ready\n\n## 0.0.1\nDuplicate"])
def test_release_rejects_missing_or_ambiguous_notes(release_tree, body):
    (release_tree / "CHANGELOG.md").write_text(f"# Changelog\n\n## 0.0.1\n\n{body}\n")
    with pytest.raises(ValueError, match=r"Changelog|Release notes"):
        release_notes(release_tree, "refs/tags/v0.0.1")


def test_source_tree_version_fallback_matches_pyproject():
    root = Path(ninja_devx.__file__).resolve().parents[2]
    pyproject = (root / "pyproject.toml").read_text()
    declared = re.search(r'^version = "([^"]+)"', pyproject, re.M)
    source = (root / "src/ninja_devx/__init__.py").read_text()
    fallback = re.search(r'^    __version__ = "([^"]+)"', source, re.M)
    assert declared is not None
    assert fallback is not None
    assert fallback.group(1) == declared.group(1)
