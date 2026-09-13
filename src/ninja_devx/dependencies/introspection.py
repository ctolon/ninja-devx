"""Dependency annotation and diagnostic helpers."""

from __future__ import annotations

from types import UnionType
from typing import (
    Annotated,
    Union,
    get_args,
    get_origin,
)

from ..exceptions import DependencyResolutionError


def check_instance(key: object, value: object) -> None:
    origin = get_origin(key) or key
    if not isinstance(origin, type):
        return
    try:
        matches = isinstance(value, origin)
    except TypeError:  # a protocol that is not runtime-checkable
        return
    if not matches:
        raise DependencyResolutionError(
            f"{type(value).__qualname__} instance is not a {type_name(key)}"
        )


def without_none(annotation: object) -> object:
    annotation = unwrap_annotated(annotation)
    if get_origin(annotation) in (Union, UnionType):
        members = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(members) == 1:
            return members[0]
    return annotation


def unwrap_annotated(annotation: object) -> object:
    if get_origin(annotation) is Annotated:
        return get_args(annotation)[0]
    return annotation


def type_name(value: object) -> str:
    name: object = getattr(value, "__qualname__", None)
    return name if isinstance(name, str) else repr(value)


def format_chain(chain: tuple[object, ...]) -> str:
    return " -> ".join(type_name(item) for item in chain)
