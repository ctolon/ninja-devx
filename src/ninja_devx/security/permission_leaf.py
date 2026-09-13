"""Calling one (non-combinator) permission; used by the permission evaluators."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Never

from django.http import HttpRequest

if TYPE_CHECKING:
    from .permissions import AnyPermission

Target = tuple[object] | None
"""``None`` for request-level checks, ``(obj,)`` for object-level ones."""


def checks_objects(permission: AnyPermission) -> bool:
    from .permissions import BasePermission

    cls = type(permission)
    return (
        cls.has_object_permission is not vars(BasePermission)["has_object_permission"]
        or cls.ahas_object_permission is not vars(BasePermission)["ahas_object_permission"]
    )


def leaf_allows(permission: AnyPermission, request: HttpRequest, target: Target) -> bool:
    if target is None:
        result = permission.has_permission(request)
    else:
        if not leaf_allows(permission, request, None):
            return False
        obj: Never = target[0]  # type: ignore[assignment]  # pyright: ignore[reportAssignmentType]
        result = permission.has_object_permission(request, obj)
    if inspect.isawaitable(result):
        if inspect.iscoroutine(result):
            result.close()
        raise TypeError(
            f"{type(permission).__qualname__} is async and cannot guard a sync operation"
        )
    return result


async def aleaf_allows(permission: AnyPermission, request: HttpRequest, target: Target) -> bool:
    if target is None:
        return await permission.ahas_permission(request)
    if not await permission.ahas_permission(request):
        return False
    obj: Never = target[0]  # type: ignore[assignment]  # pyright: ignore[reportAssignmentType]
    return await permission.ahas_object_permission(request, obj)
