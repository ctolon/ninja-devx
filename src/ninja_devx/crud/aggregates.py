"""``GET /stats`` grouping and aggregating a model controller's scoped list queryset.

::

    class OrderController(
        AggregateMixin[Order, OrderOut], CRUDController[Order, OrderOut, OrderIn]
    ):
        aggregate_fields = ("status", "region")
        aggregate_metrics = {"total": Sum("amount"), "count": Count("id")}

``GET /stats?group_by=status&metrics=total&metrics=count`` groups the same queryset the list
operation would serve (tenant, owner, soft deletion and ``search``/``filter_fields`` all
apply) and answers ``[{"status": "done", "total": "120.00", "count": 3}, ...]``. Omitting
``group_by`` aggregates the whole queryset into one row. ``group_by`` is restricted to
``aggregate_fields`` and ``metrics`` to the keys of ``aggregate_metrics`` (default
``["count"]``); anything else is a 422.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Annotated, ClassVar, Generic, Literal, TypeAlias, cast

from django.core.checks import CheckMessage, Error
from django.db.models import Aggregate, Count, QuerySet
from django.http import HttpRequest
from ninja import FilterSchema, Schema
from pydantic import Field as SchemaField

from .._internal.cache import owned_cache
from .._internal.generics import LazyAnnotation
from ..dependencies.container import ContainerLike
from ..exceptions import ControllerConfigError
from ..routing.operations import async_variant, get
from ..serialization.pydantic import build_schema
from .annotations import Filters, OrderingSchema, query_schema
from .controllers import ListConfig, ModelT, OutT
from .fields import resolve_field

__all__ = ["AggregateMixin"]


def aggregate_schema_for(controller: type[object]) -> type[Schema]:
    """The ``group_by``/``metrics`` query schema of a controller (cached)."""
    _schemas: dict[type[object], type[Schema]] = owned_cache(controller, "aggregates_schemas")
    if (cached := _schemas.get(controller)) is None:
        cached = _schemas[controller] = _build_schema(controller)
    return cached


def _build_schema(controller: type[object]) -> type[Schema]:
    metrics: Mapping[str, Aggregate] = getattr(controller, "aggregate_metrics", {})
    if not metrics:
        raise ControllerConfigError(
            f"{controller.__qualname__}.aggregate_metrics must not be empty"
        )
    default_metrics = ["count"] if "count" in metrics else [next(iter(metrics))]
    metric_type: object = Literal[tuple(metrics)]
    fields: dict[str, tuple[object, object]] = {
        "metrics": (
            list[metric_type],  # type: ignore[valid-type]
            SchemaField(default_factory=lambda: list(default_metrics)),
        ),
    }
    group_by: Sequence[str] = getattr(controller, "aggregate_fields", ())
    if group_by:
        group_type: object = Literal[tuple(group_by)]
        fields["group_by"] = (
            list[group_type],  # type: ignore[valid-type]
            SchemaField(default_factory=list),
        )
    return build_schema(f"{controller.__name__}AggregateQuery", Schema, fields)


class _AggregateQueryMarker(LazyAnnotation):
    def resolve(self, annotation: object, controller: type[object]) -> object:
        return query_schema(aggregate_schema_for(controller))


AggregateParams: TypeAlias = Annotated[Schema, _AggregateQueryMarker()]
"""Query parameters ``group_by`` (from ``aggregate_fields``) and ``metrics`` (from
``aggregate_metrics``, default ``["count"]``)."""


class AggregateMixin(ListConfig[ModelT, OutT], Generic[ModelT, OutT]):
    """``GET /stats``: grouped counts and sums over the controller's scoped queryset."""

    aggregate_fields: ClassVar[Sequence[str]] = ()
    """Allow-list of model fields ``group_by`` may name."""
    aggregate_metrics: ClassVar[Mapping[str, Aggregate]] = MappingProxyType({"count": Count("pk")})
    """Allow-list of aggregate expressions ``metrics`` may name, by the name clients use."""

    @get("/stats", response=list[dict[str, object]])
    def stats(
        self, request: HttpRequest, filters: Filters, query: AggregateParams
    ) -> list[dict[str, object]]:
        return self._rows(request, filters, query)

    @async_variant(stats)
    async def astats(
        self, request: HttpRequest, filters: Filters, query: AggregateParams
    ) -> list[dict[str, object]]:
        await self.aprepare_request(request)
        return await self.run_sync(self._rows, request, filters, query)

    def _rows(
        self, request: HttpRequest, filters: FilterSchema, query: Schema
    ) -> list[dict[str, object]]:
        cls = type(self)
        group_by = cast("Sequence[str]", getattr(query, "group_by", ()))
        names = cast("Sequence[str]", getattr(query, "metrics", ()))
        metrics = {name: cls.aggregate_metrics[name] for name in names}
        queryset = self._grouping_queryset(request, filters)
        if group_by:
            rows = queryset.values(*group_by).annotate(**metrics)
        else:
            return [cast("dict[str, object]", dict(queryset.aggregate(**metrics)))]
        return [cast("dict[str, object]", dict(row)) for row in rows]

    def _grouping_queryset(self, request: HttpRequest, filters: FilterSchema) -> QuerySet[ModelT]:
        # Any ordering (including a model's default Meta.ordering) would otherwise be
        # pulled into GROUP BY, splitting rows that should be grouped together.
        queryset = self.filter_queryset(
            request, self.scoped_queryset(request), filters, OrderingSchema()
        )
        return queryset.order_by()

    @classmethod
    def checks(cls, container: ContainerLike | None = None) -> list[CheckMessage]:
        messages = super().checks(container)
        model = cls.get_model()
        for name in cls.aggregate_fields:
            try:
                resolve_field(model, name)
            except ControllerConfigError:
                messages.append(
                    Error(
                        f"{cls.__qualname__}.aggregate_fields: {model.__name__} has no field "
                        f"{name!r}",
                        obj=cls,
                        id="ninja_devx.E007",
                    )
                )
        return messages
