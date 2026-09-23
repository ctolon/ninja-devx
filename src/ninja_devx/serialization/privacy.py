"""Mark schema fields that must never appear in logs, error echoes or plain exports.

::

    class UserIn(Schema):
        email: str
        password: Annotated[str, Sensitive()]

``Sensitive`` is metadata only; it does not change validation or serialization by itself.
Callers that might otherwise echo raw field values (the CRUD CSV/JSONL export, a custom
error body) call ``redact_payload`` on the data they are about to emit.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from pydantic.fields import FieldInfo

__all__ = ["Sensitive", "mask", "redact_payload", "sensitive_fields"]


@dataclass(frozen=True, slots=True)
class Sensitive:
    """Field metadata: ``Annotated[str, Sensitive()]``. Carries no configuration."""


def sensitive_fields(schema: type[object]) -> frozenset[str]:
    """Field and alias names on ``schema`` annotated with ``Sensitive``.

    Both spellings are returned because a dump may be keyed by either, depending on
    whether it used ``by_alias``.

    :param schema: A pydantic model (or ``Schema``).
    """
    fields: Mapping[str, FieldInfo] = getattr(schema, "model_fields", {})
    names: set[str] = set()
    for name, info in fields.items():
        if any(isinstance(item, Sensitive) for item in info.metadata):
            names.add(name)
            if info.alias:
                names.add(info.alias)
    return frozenset(names)


def mask(value: object) -> object:
    """Replace a sensitive value with ``"***"`` (``None`` stays ``None``; lists are masked
    element-wise).

    :param value: The value to mask.
    """
    if isinstance(value, list | tuple):
        return [None if item is None else "***" for item in cast("list[object]", value)]
    return None if value is None else "***"


def redact_payload(schema: type[object], data: Mapping[str, object]) -> dict[str, object]:
    """A copy of ``data`` with every ``Sensitive`` field of ``schema`` masked.

    :param schema: The pydantic model ``data`` was (or will be) dumped from.
    :param data: A mapping keyed by field name or alias, such as a ``model_dump()`` result.
    """
    names = sensitive_fields(schema)
    if not names:
        return dict(data)
    return {key: mask(value) if key in names else value for key, value in data.items()}
