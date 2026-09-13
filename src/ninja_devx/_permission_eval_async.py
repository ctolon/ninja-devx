"""Evaluate a permission tree, deferring object-dependent decisions until lookup.

``_permission_eval.py`` is generated from this file with ``python -m ninja_devx.tooling.unasync``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.http import HttpRequest

from .security import permission_leaf as _permission_leaf
from .security.permission_leaf import Target

if TYPE_CHECKING:
    from .security.permissions import AnyPermission


async def adenied(
    permission: AnyPermission, request: HttpRequest, target: Target
) -> AnyPermission | None:
    allowed, refused = await _evaluate(permission, request, target)
    return refused if allowed is False else None


async def _evaluate(
    permission: AnyPermission, request: HttpRequest, target: Target
) -> tuple[bool | None, AnyPermission | None]:
    """None is unknown, not allowed: object checks cannot run before an object exists.

    Preserve unknown through negation and nested Boolean expressions. At object time
    each leaf must satisfy both its request and object checks, so OR cannot switch
    between two independently failing branches.
    """
    combinator = permission.combinator
    if combinator in {"all", "any"}:
        pending = False
        refused = permission
        for child in permission.operands:
            allowed, child_refused = await _evaluate(child, request, target)
            if combinator == "all" and allowed is False:
                return False, child_refused
            if combinator == "any" and allowed is True:
                return True, None
            pending |= allowed is None
            if child_refused is not None:
                refused = child_refused
        if pending:
            return None, None
        return (True, None) if combinator == "all" else (False, refused)
    if combinator == "not":
        allowed, _ = await _evaluate(permission.operands[0], request, target)
        if allowed is None:
            return None, None
        return (False, permission) if allowed else (True, None)
    if not await _permission_leaf.aleaf_allows(permission, request, target):
        return False, permission
    if target is None and _permission_leaf.checks_objects(permission):
        return None, None
    return True, None
