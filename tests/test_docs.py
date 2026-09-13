"""Every Python block in the docs compiles, and its ninja_devx imports exist."""

import ast
import importlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PAGES = sorted(
    [ROOT / "README.md", *(ROOT / "docs").rglob("*.md"), *(ROOT / "examples").glob("*/README.md")]
)
PAGES = [page for page in PAGES if not {"history", "options"} & set(page.parts)]
BLOCK = re.compile(r"^```python\n(.*?)^```", re.DOTALL | re.MULTILINE)


SNIPPET = re.compile(r'^--8<-- "([^"]+)"$', re.MULTILINE)


def blocks():
    for page in PAGES:
        text = page.read_text()
        # pymdownx.snippets includes files: check the included code, not the directive
        text = SNIPPET.sub(lambda match: (ROOT / match.group(1)).read_text(), text)
        for match in BLOCK.finditer(text):
            line = text.count("\n", 0, match.start()) + 2
            yield pytest.param(match.group(1), id=f"{page.relative_to(ROOT)}:{line}")


@pytest.mark.parametrize("source", list(blocks()))
def test_python_blocks_compile_and_import_real_names(source):
    tree = ast.parse(source)  # SyntaxError fails the test with the page and line
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("ninja_devx"):
            module = importlib.import_module(node.module)
            missing = [alias.name for alias in node.names if not hasattr(module, alias.name)]
            assert not missing, f"from {node.module} import {missing} does not exist"


NAME = re.compile(r"`((?:ninja_devx)(?:\.[a-z_]+)+)`")


def test_module_paths_mentioned_in_the_docs_exist():
    for page in PAGES:
        if "migration" in page.parts:  # names removed in past versions
            continue
        for dotted in NAME.findall(page.read_text()):
            try:
                importlib.import_module(dotted)
            except ModuleNotFoundError:
                module, _, attribute = dotted.rpartition(".")
                assert hasattr(importlib.import_module(module), attribute), f"{page}: {dotted}"


def test_every_reference_module_exists():
    reference = (ROOT / "docs" / "reference.md").read_text()
    for dotted in re.findall(r"^::: (\S+)$", reference, re.MULTILINE):
        importlib.import_module(dotted)
