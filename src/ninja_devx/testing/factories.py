"""Synthetic request payloads and records for tests.

``sample(Schema)`` builds a valid, JSON-serialisable payload from a pydantic schema:
declared defaults and examples win, otherwise a value is derived from the field type.
``samples(Schema, n)`` varies strings and numbers so rows are distinguishable::

    from ninja_devx.testing import sample, samples

    payload = sample(ArticleIn)                 # {"title": "title", ...}
    rows = samples(ArticleIn, 3)                # three distinct payloads

A required field whose type has no derivation rule raises ``TypeError``; give it an
example or a default instead.
"""

from __future__ import annotations

import enum
import types
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Literal, TypeVar, Union, get_args, get_origin
from uuid import UUID, uuid4

from annotated_types import Ge, Gt
from pydantic import BaseModel
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

__all__ = ["sample", "samples"]

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class _Unsupported(Exception):
    pass


def _by_type(annotation: object, name: str, index: int) -> object:
    if annotation is str:
        return name if index == 0 else f"{name}-{index}"
    if annotation is int:
        return index
    if annotation is float:
        return float(index)
    if annotation is Decimal:
        return str(index)
    if annotation is bool:
        return True
    if annotation is bytes:
        return name.encode()
    if annotation is UUID:
        return str(uuid4())
    if annotation is datetime:
        return datetime.now(UTC).isoformat()
    if annotation is date:
        return date.today().isoformat()
    if annotation is time:
        return time(12, index % 60).isoformat()
    origin = get_origin(annotation)
    if origin in (list, set, tuple, frozenset):
        return []
    if origin is dict:
        return {}
    if origin is Literal:
        choices = get_args(annotation)
        return choices[index % len(choices)]
    if origin in (Union, types.UnionType):
        for member in get_args(annotation):
            if member is not type(None):
                return _by_type(member, name, index)
        return None  # pragma: no cover - a union of only None cannot be constructed
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        members = list(annotation)
        return members[index % len(members)].value if members else None
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _model(annotation, index)
    raise _Unsupported


def _lower_bound(info: FieldInfo) -> int | None:
    for item in info.metadata:
        if isinstance(item, Ge) and isinstance(item.ge, int):
            return item.ge
        if isinstance(item, Gt) and isinstance(item.gt, int):
            return item.gt + 1
    return None


def _model(schema: type[BaseModel], index: int) -> dict[str, object]:
    values: dict[str, object] = {}
    for name, info in schema.model_fields.items():
        if info.is_required():
            values[name] = _value(schema, name, info, index)
            continue
        if info.default is not PydanticUndefined and info.default is not None:
            values[name] = info.default
    return values


def _value(schema: type[BaseModel], name: str, info: FieldInfo, index: int) -> object:
    if info.examples:
        return info.examples[0]
    bound = _lower_bound(info)
    if bound is not None:
        index = max(index, bound) if index else bound
    try:
        return _by_type(info.annotation, name, index)
    except _Unsupported:
        raise TypeError(
            f"{schema.__name__}.{name}: no sample rule for {info.annotation!r}; "
            "declare an example or a default"
        ) from None


def sample(schema: type[SchemaT]) -> dict[str, object]:
    """A payload for ``schema`` built from defaults, examples and field types.

    :param schema: The pydantic schema (usually the request body).
    """
    return _model(schema, 0)


def samples(schema: type[SchemaT], count: int) -> list[dict[str, object]]:
    """``count`` payloads for ``schema`` with distinguishable scalar values.

    :param schema: The pydantic schema (usually the request body).
    :param count: How many payloads to build.
    """
    if count < 1:
        raise ValueError("count must be positive")
    return [_model(schema, index) for index in range(count)]
