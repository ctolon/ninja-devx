"""Typed wrapper around ``pydantic.create_model`` for schemas built at runtime."""

from collections.abc import Callable, Mapping
from typing import Final, TypeVar

from pydantic import BaseModel, create_model

__all__ = ["build_schema"]

SchemaT = TypeVar("SchemaT", bound=BaseModel)

# ``create_model`` is overloaded on dunder keyword arguments; field definitions are
# passed as ``**fields``, which its overloads cannot express.
_create_model: Final[Callable[..., type[BaseModel]]] = create_model


def build_schema(
    name: str, base: type[SchemaT], fields: Mapping[str, tuple[object, object]]
) -> type[SchemaT]:
    """A subclass of ``base`` named ``name`` with ``fields`` (``name -> (annotation, default)``)."""
    schema = _create_model(name, __base__=base, **fields)
    if not issubclass(schema, base):  # pragma: no cover - guaranteed by pydantic
        raise TypeError(f"{name} is not a {base.__name__}")
    return schema
