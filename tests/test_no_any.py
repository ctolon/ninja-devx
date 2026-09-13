"""The package must not use ``typing.Any``: opaque values are ``object``, the rest is typed."""

import ast
from pathlib import Path

import ninja_devx

SOURCE = Path(ninja_devx.__file__).parent


def test_source_does_not_use_typing_any():
    offenders = []
    for path in sorted(SOURCE.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            imported = isinstance(node, ast.ImportFrom) and any(a.name == "Any" for a in node.names)
            attribute = isinstance(node, ast.Attribute) and node.attr == "Any"
            if imported or attribute:
                offenders.append(f"{path.relative_to(SOURCE)}:{node.lineno}")
    assert offenders == []
