"""OpenAPI examples generated from ``ninja_devx.testing.sample``.

::

    ArticleOut = with_examples(ArticleOut)

Built field by field, so one field with no sample rule (no default, no example, no type
``ninja_devx.testing.sample`` knows how to derive) is just left out instead of failing the
whole schema. Model controllers do this automatically for their input and output schemas
with ``openapi_examples = True`` (see ``ninja_devx.crud.controllers.ModelController``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Final, TypeVar, cast

from pydantic import BaseModel, ConfigDict, JsonValue, create_model

__all__ = ["with_examples"]

SchemaT = TypeVar("SchemaT", bound=BaseModel)

# ``create_model`` is overloaded on dunder keyword arguments; a single dynamic field
# definition built from a ``FieldInfo`` cannot satisfy either overload literally.
_create_model: Final[Callable[..., type[BaseModel]]] = create_model


def with_examples(schema: type[SchemaT]) -> type[SchemaT]:
    """``schema``, with an OpenAPI example filled in from its fields' derived sample values.

    Mutates ``schema.model_config`` in place and returns the same class, so it composes
    with the rest of a schema's definition: ``ArticleOut = with_examples(ArticleOut)``.

    :param schema: The schema to add an example to.
    """
    from ninja_devx.testing import sample

    example: dict[str, JsonValue] = {}
    for name, info in schema.model_fields.items():
        single = _create_model("_Sample", __base__=BaseModel, **{name: (info.annotation, info)})
        try:
            values = sample(single)
        except TypeError:
            continue
        if name in values:
            example[name] = cast("JsonValue", values[name])
    if example:
        current = schema.model_config.get("json_schema_extra")
        extra: dict[str, JsonValue] = (
            dict(cast("Mapping[str, JsonValue]", current)) if isinstance(current, dict) else {}
        )
        extra["examples"] = cast("JsonValue", [example])
        schema.model_config = cast(
            "ConfigDict", {**schema.model_config, "json_schema_extra": extra}
        )
    return schema
