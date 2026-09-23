"""Layering: core modules must not import the CRUD or contrib layers.

``tooling`` and ``management`` are outer-ring helpers and may import CRUD/contrib; the
runtime core (routing, security, dependencies, serialization, configuration, http and the
internal helpers) must stay independent so the package does not become a framework inside
a framework (see ``docs/project/scope.md``).
"""

import ast
from pathlib import Path

import ninja_devx
from ninja_devx.security.object_permissions import registered_object_permission_backends

SOURCE = Path(ninja_devx.__file__).parent
CORE_PACKAGES = (
    "routing",
    "security",
    "dependencies",
    "serialization",
    "configuration",
    "http",
    "layers",
    "idempotency",
    "cqrs",
    "_internal",
)
CORE_MODULES = (
    "exceptions.py",
    "apps.py",
    "plugins.py",
    "stamps.py",
    "_permission_eval.py",
    "_permission_eval_async.py",
)
FORBIDDEN_FOR_CRUD = {"contrib"}


def _core_files() -> list[Path]:
    found = [path for package in CORE_PACKAGES for path in sorted((SOURCE / package).rglob("*.py"))]
    found += [SOURCE / name for name in CORE_MODULES]
    return found


def _absolute(path: Path, node: ast.ImportFrom) -> str:
    package = path.relative_to(SOURCE).with_suffix("").parts[:-1]
    if node.level:
        package = package[: len(package) - (node.level - 1)]
    return ".".join([*package, node.module]) if node.module else ".".join(package)


def _layer(module: str) -> str | None:
    parts = module.split(".")
    if parts[:1] != ["ninja_devx"] or len(parts) < 2:
        return None
    return parts[1] if parts[1] in {"crud", "contrib"} else None


def _offenders(path: Path, forbidden: set[str]) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.ImportFrom):
            module = _absolute(path, node)
            if _layer(module) in forbidden:
                found.append(f"{path.relative_to(SOURCE)}:{node.lineno}: from {module} import ...")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _layer(alias.name) in forbidden:
                    found.append(f"{path.relative_to(SOURCE)}:{node.lineno}: import {alias.name}")
    return found


def test_core_does_not_import_crud_or_contrib():
    offenders = [item for path in _core_files() for item in _offenders(path, {"crud", "contrib"})]
    assert offenders == [], "core imports a higher layer:\n" + "\n".join(offenders)


def test_crud_does_not_import_contrib():
    offenders = [
        item
        for path in sorted((SOURCE / "crud").rglob("*.py"))
        for item in _offenders(path, FORBIDDEN_FOR_CRUD)
    ]
    assert offenders == [], "crud imports contrib:\n" + "\n".join(offenders)


def test_grants_backend_is_registered_by_its_app():
    assert "grants" in registered_object_permission_backends()
