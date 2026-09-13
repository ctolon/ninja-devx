"""``?fields=`` and ``?expand=`` query parameters for model controllers."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Annotated, cast

from django.utils.translation import gettext as _
from ninja.errors import ValidationError
from ninja.params.functions import Query
from pydantic import BaseModel, ConfigDict, JsonValue

from .._internal.cache import owned_cache
from ..exceptions import ControllerConfigError
from ..routing.bindings import Arguments, ParameterBinding
from ..serialization.visibility import (
    FieldVisibility,
    ResponseShape,
    expandable_fields,
    set_response_shape,
)

if TYPE_CHECKING:
    from ..dependencies.instances import Invocation

__all__ = ["partial_schema", "shape_bindings"]


def _optional_properties(schema: dict[str, JsonValue]) -> None:
    schema.pop("required", None)


def partial_schema(schema: type[BaseModel]) -> type[BaseModel]:
    """``schema`` with every field optional in OpenAPI (``<Name>Partial``), for responses
    shaped by ``?fields=``. Validation and serialization are unchanged."""
    _partials: dict[type[BaseModel], type[BaseModel]] = owned_cache(schema, "shaping_partials")
    cached = _partials.get(schema)
    if cached is None:
        config = ConfigDict(json_schema_extra=_optional_properties)
        cached = _partials[schema] = cast(
            "type[BaseModel]",
            type(
                f"{schema.__name__}Partial",
                (schema,),
                {"model_config": config, "__module__": schema.__module__},
            ),
        )
    return cached


def shape_bindings(controller: type[object]) -> tuple[ParameterBinding, ...]:
    output: object = getattr(controller, "output_schema")()  # noqa: B009
    if not (isinstance(output, type) and issubclass(output, BaseModel)):
        return ()
    sparse: bool = getattr(controller, "sparse_fields", False)
    fields_param: str = getattr(controller, "fields_param", "fields")
    expand_param: str = getattr(controller, "expand_param", "expand")
    expandable = sorted(expandable_fields(output))
    if not sparse and not expandable:
        return ()
    if not issubclass(output, FieldVisibility):
        raise ControllerConfigError(
            f"{getattr(controller, '__qualname__', controller)}: {output.__name__} needs the "
            "FieldVisibility mixin for sparse_fields or Expandable fields: "
            f"class {output.__name__}(FieldVisibility, Schema)"
        )
    names = sorted(
        info.alias if isinstance(info.alias, str) else name
        for name, info in output.model_fields.items()
    )
    parameters: list[inspect.Parameter] = []
    if sparse:
        parameters.append(
            inspect.Parameter(
                fields_param,
                inspect.Parameter.KEYWORD_ONLY,
                default=None,
                annotation=Annotated[
                    str | None,
                    Query(
                        None, description=f"Comma-separated fields to return: {', '.join(names)}."
                    ),
                ],
            )
        )
    if expandable:
        parameters.append(
            inspect.Parameter(
                expand_param,
                inspect.Parameter.KEYWORD_ONLY,
                default=None,
                annotation=Annotated[
                    str | None,
                    Query(None, description=f"Relations to embed: {', '.join(expandable)}."),
                ],
            )
        )
    schema = output

    def parse(arguments: Arguments, key: str, allowed: list[str]) -> frozenset[str] | None:
        raw: object = arguments.pop(key, None)
        if not isinstance(raw, str) or not raw.strip():
            return None
        chosen = frozenset(part.strip() for part in raw.split(",") if part.strip())
        if unknown := sorted(chosen - set(allowed)):
            raise ValidationError(
                [
                    {
                        "type": "value_error",
                        "loc": ["query", key],
                        "msg": _("Unknown names %(unknown)s; allowed: %(allowed)s")
                        % {"unknown": ", ".join(unknown), "allowed": ", ".join(allowed)},
                    }
                ]
            )
        return chosen

    def resolve(invocation: Invocation, arguments: Arguments) -> None:
        fields = parse(arguments, fields_param, names) if sparse else None
        expand = parse(arguments, expand_param, expandable) if expandable else None
        set_response_shape(
            invocation.request, ResponseShape(schema, fields=fields, expand=expand or frozenset())
        )

    async def aresolve(invocation: Invocation, arguments: Arguments) -> None:
        resolve(invocation, arguments)

    return (
        ParameterBinding(
            name=None,
            parameters=tuple(parameters),
            resolve=resolve,
            aresolve=aresolve,
            documented_errors=frozenset({422}),
        ),
    )
