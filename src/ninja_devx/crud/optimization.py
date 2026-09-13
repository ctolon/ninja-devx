"""Derive ``select_related``/``prefetch_related`` from output schemas to avoid N+1 queries.

Forward foreign keys rendered as nested schemas are joined. Many-to-many and reverse
relations are prefetched with a ``Prefetch`` whose queryset joins *their* nested foreign
keys, so ``comments: list[CommentOut]`` with ``CommentOut.author: UserOut`` costs one
query per relation, not one per level.

``optimize_queryset(queryset, schema, only=True)`` also restricts the selected columns
to the fields the schema reads; it is skipped automatically when the schema reads
anything that is not a model field (resolvers, properties), since those could load
deferred columns one row at a time.
"""

from __future__ import annotations

import types
from dataclasses import dataclass, field
from typing import Final, TypeAlias, TypeVar, Union, get_args, get_origin

from django.core.exceptions import FieldDoesNotExist
from django.db.models import Model, Prefetch, QuerySet
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from .._internal.cache import owned_cache

__all__ = ["QueryPlan", "optimize_queryset", "query_plan", "related_lookups"]

ModelT = TypeVar("ModelT", bound=Model)

_MAX_DEPTH: Final = 4
AnyPrefetch: TypeAlias = "Prefetch[str, QuerySet[Model], str]"


@dataclass(slots=True)
class QueryPlan:
    """How to load ``model`` for ``schema``: joins, prefetches (each with its own plan)."""

    model: type[Model]
    select: list[str] = field(default_factory=list[str])
    prefetch: list[tuple[str, QueryPlan]] = field(default_factory=list["tuple[str, QueryPlan]"])
    columns: list[str] = field(default_factory=list[str])
    exact: bool = True  # every schema field is a model field: ``only()`` is safe

    def apply(self, queryset: QuerySet[ModelT], *, only: bool = False) -> QuerySet[ModelT]:
        if self.select:
            queryset = queryset.select_related(*self.select)
        if self.prefetch:
            existing: tuple[str | AnyPrefetch, ...] = getattr(
                queryset, "_prefetch_related_lookups", ()
            )
            seen = {item if isinstance(item, str) else item.prefetch_to for item in existing}
            lookups: list[str | AnyPrefetch] = []
            for lookup, plan in self.prefetch:
                if lookup in seen:
                    continue  # the view prefetches it already, its way
                if plan.select or plan.prefetch or (only and plan.is_exact()):
                    lookups.append(
                        Prefetch(lookup, queryset=plan.apply(plan.queryset(), only=only))
                    )
                else:
                    lookups.append(lookup)
            if lookups:
                queryset = queryset.prefetch_related(*lookups)
        if only and self.is_exact():
            queryset = queryset.only(*self.columns)
        return queryset

    def queryset(self) -> QuerySet[Model]:
        manager = self.model._default_manager
        return manager.all()

    def is_exact(self) -> bool:
        return self.exact and all(plan.is_exact() for _, plan in self.prefetch)

    def lookups(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Flat ``(select_related, prefetch_related)`` lookups, for inspection."""
        prefetch: list[str] = []
        for lookup, plan in self.prefetch:
            prefetch.append(lookup)
            select, nested = plan.lookups()
            prefetch.extend(f"{lookup}__{name}" for name in (*select, *nested))
        return tuple(self.select), tuple(prefetch)


def query_plan(
    model: type[Model], schema: type[BaseModel], expand: frozenset[str] = frozenset()
) -> QueryPlan:
    """The (cached) loading plan for serializing ``model`` instances with ``schema``.

    ``Expandable`` relations are loaded only when named in ``expand``.
    """
    _cache: dict[tuple[type[Model], type[BaseModel], frozenset[str]], QueryPlan] = owned_cache(
        schema, "query_plans"
    )
    key = (model, schema, expand)
    if (cached := _cache.get(key)) is None:
        cached = _cache[key] = QueryPlan(model)
        _collect(cached, model, schema, "", depth=0, expand=expand)
    return cached


def related_lookups(
    model: type[Model], schema: type[BaseModel]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(select_related, prefetch_related)`` lookups to serialize ``model`` with ``schema``."""
    return query_plan(model, schema).lookups()


def optimize_queryset(
    queryset: QuerySet[ModelT],
    schema: type[BaseModel],
    *,
    only: bool = False,
    expand: frozenset[str] = frozenset(),
) -> QuerySet[ModelT]:
    return query_plan(queryset.model, schema, expand).apply(queryset, only=only)


def _collect(
    plan: QueryPlan,
    model: type[Model],
    schema: type[BaseModel],
    prefix: str,
    *,
    depth: int,
    expand: frozenset[str] = frozenset(),
) -> None:
    """Fill ``plan`` for ``model`` rendered with ``schema`` (``prefix`` for joined models)."""
    plan.columns.append(f"{prefix}{model._meta.pk.attname}")
    for name, info in schema.model_fields.items():
        attribute = info.alias if isinstance(info.alias, str) else name
        try:
            model_field = model._meta.get_field(attribute)
        except FieldDoesNotExist:
            plan.exact = False  # a property or a resolver: it may read any column
            continue
        related: type[Model] | None = getattr(model_field, "related_model", None)
        if not model_field.is_relation or related is None:
            plan.columns.append(f"{prefix}{attribute}")
            continue
        nested = _nested_schema(info.annotation)
        concrete = getattr(model_field, "concrete", False)
        forward = (model_field.many_to_one or model_field.one_to_one) and concrete
        if name not in expand and _is_expandable(info):  # rendered as its key unless expanded
            if not forward:
                plan.exact = False  # the key comes from a custom ``source``
                continue
            nested = None
        if forward:
            attname: str = getattr(model_field, "attname", attribute)
            plan.columns.append(f"{prefix}{attname}")
            if nested is None or depth + 1 >= _MAX_DEPTH:
                continue  # ``author: int`` needs no join
            lookup = f"{prefix}{attribute}"
            plan.select.append(lookup)
            _collect(plan, related, nested, f"{lookup}__", depth=depth + 1)
        else:  # many-to-many or reverse relation: its own query, with its own joins
            child = QueryPlan(related)
            remote = getattr(model_field, "remote_field", None)
            remote_attname: object = getattr(getattr(model_field, "field", None), "attname", None)
            if isinstance(remote_attname, str):  # reverse FK: the join column must be loaded
                child.columns.append(remote_attname)
            elif remote is not None and not concrete:
                child.exact = False  # reverse m2m / generic relations: don't restrict
            if nested is not None and depth + 1 < _MAX_DEPTH:
                _collect(child, related, nested, "", depth=depth + 1)
            else:
                child.exact = False
            if prefix:  # a relation of a joined model: prefetch through the join path
                plan.prefetch.append((f"{prefix}{attribute}", child))
            else:
                plan.prefetch.append((attribute, child))


def _is_expandable(info: FieldInfo) -> bool:
    return any(type(item).__name__ == "Expandable" for item in info.metadata)


def _nested_schema(annotation: object) -> type[BaseModel] | None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    if get_origin(annotation) in (list, tuple, set, frozenset, Union, types.UnionType):
        for argument in get_args(annotation):
            if (found := _nested_schema(argument)) is not None:
                return found
    return None
