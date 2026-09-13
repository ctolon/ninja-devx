"""Target-language source boundaries: identifiers, collisions and literal paths."""

from __future__ import annotations

import json
import keyword
import re
from collections.abc import Iterable, Mapping
from typing import cast

from .openapi import Schema, as_mapping


def literal(value: str) -> str:
    """A quoted Python/JavaScript string, retaining non-BMP Unicode characters."""
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def identifier(name: str, *, reserved: Iterable[str] = (), javascript: bool = False) -> None:
    pattern = r"[A-Za-z_$][A-Za-z0-9_$]*" if javascript else r"[A-Za-z_][A-Za-z0-9_]*"
    if (
        not re.fullmatch(pattern, name)
        or (not javascript and keyword.iskeyword(name))
        or name in reserved
    ):
        raise ValueError(f"Unsupported or reserved generated identifier: {name!r}")


def unique(names: Iterable[str], *, namespace: str, reserved: Iterable[str] = ()) -> None:
    seen = set(reserved)
    for name in names:
        if name in seen:
            raise ValueError(f"Generated identifier collision in {namespace}: {name!r}")
        seen.add(name)


def schemas(document: Schema, *, reserved: Iterable[str], javascript: bool = False) -> None:
    names = as_mapping(as_mapping(document.get("components")).get("schemas"))
    for name in names:
        identifier(name, reserved=reserved, javascript=javascript)

    def references(value: object) -> None:
        if isinstance(value, Mapping):
            mapping = as_mapping(cast("object", value))
            ref = mapping.get("$ref")
            if isinstance(ref, str):
                if ref.startswith("#/components/schemas/"):
                    name = ref.removeprefix("#/components/schemas/")
                    if name not in names:
                        raise ValueError(f"Unknown schema reference: {ref!r}")
                elif not ref.startswith("#/components/"):
                    raise ValueError(f"Unsupported external reference: {ref!r}")
            for child in mapping.values():
                references(child)
        elif isinstance(value, list | tuple):
            from .openapi import as_list

            for child in as_list(cast("object", value)):
                references(child)

    references(document)


def path_expression(path: str, names: dict[str, str], *, javascript: bool = False) -> str:
    """Concatenate quoted static segments and encoded parameters, never raw templates."""
    parts: list[str] = []
    offset = 0
    used: set[str] = set()
    for match in re.finditer(r"\{([^{}]+)\}", path):
        name = match[1]
        if name not in names:
            raise ValueError(f"Path references undeclared parameter {name!r}")
        parts.append(literal(path[offset : match.start()]))
        expression = names[name]
        parts.append(
            f"encodeURIComponent(String({expression}))" if javascript else f"_path({expression})"
        )
        used.add(name)
        offset = match.end()
    parts.append(literal(path[offset:]))
    if used != names.keys():
        raise ValueError(f"Path parameters missing from template: {sorted(names.keys() - used)}")
    return " + ".join(part for part in parts if part != '""') or '""'
