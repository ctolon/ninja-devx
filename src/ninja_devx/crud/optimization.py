"""Derive ``select_related``/``prefetch_related`` from output schemas to avoid N+1 queries.

Forward foreign keys rendered as nested schemas are joined. Many-to-many and reverse
relations are prefetched with a ``Prefetch`` whose queryset joins *their* nested foreign
keys, so ``comments: list[CommentOut]`` with ``CommentOut.author: UserOut`` costs one
query per relation, not one per level.

``optimize_queryset(queryset, schema, only=True)`` also restricts the selected columns
to the fields the schema reads; it is skipped automatically when the schema reads
anything that is not a model field (resolvers, properties), since those could load
deferred columns one row at a time.

``ExpandRule`` shapes an expanded to-many relation (filter, order, a per-parent cap)
through a ``Prefetch`` queryset.
"""

from __future__ import annotations

import types
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final, TypeAlias, TypeVar, Union, get_args, get_origin

import django
from django.core.exceptions import FieldDoesNotExist
from django.db.models import F, Model, OuterRef, Prefetch, Q, QuerySet, Subquery, Window
from django.db.models.functions import RowNumber
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from .._internal.cache import owned_cache

__all__ = [
    "ExpandRule",
    "QueryPlan",
    "optimize_queryset",
    "query_plan",
    "related_lookups",
    "requires_related",
]

ModelT = TypeVar("ModelT", bound=Model)
T = TypeVar("T")

_MAX_DEPTH: Final = 4
_RANK_ANNOTATION: Final = "_ninja_devx_rank"
_SUPPORTS_QUALIFY: Final = django.VERSION >= (5, 0)
AnyPrefetch: TypeAlias = "Prefetch[str, QuerySet[Model], str]"


@dataclass(frozen=True, slots=True)
class ExpandRule:
    """How ``?expand=`` loads a to-many relation: filtered, ordered and capped per parent.

    ::

        class ArticleController(ReadOnlyModelController[Article, ArticleOut]):
            expand_rules = {
                "comments": ExpandRule(filter=Q(published=True), order_by=("-created",), limit=5),
            }

    Rule keys must name an ``Expandable`` field of the output schema (``ControllerConfigError``
    otherwise). ``limit`` ranks rows per parent with a window function (a ``QUALIFY``-style
    filter on Django 5.0+) or, on Django 4.2, an equivalent correlated subquery; it is skipped,
    with ``ninja_devx.W007``, when the relation is not a plain reverse foreign key (a
    many-to-many, for example).
    """

    filter: Q | None = None
    """Restricts the related rows, applied before ``order_by`` and ``limit``."""
    order_by: tuple[str, ...] = ()
    """Ordering applied before ``limit``; defaults to the related model's ``Meta.ordering``."""
    limit: int | None = None
    """Rows kept per parent object."""


@dataclass(slots=True)
class QueryPlan:
    """How to load ``model`` for ``schema``: joins, prefetches (each with its own plan)."""

    model: type[Model]
    select: list[str] = field(default_factory=list[str])
    prefetch: list[tuple[str, QueryPlan]] = field(default_factory=list["tuple[str, QueryPlan]"])
    columns: list[str] = field(default_factory=list[str])
    exact: bool = True  # every schema field is a model field: ``only()`` is safe
    forced_select: list[str] = field(default_factory=list[str])
    forced_prefetch: list[str] = field(default_factory=list[str])

    def apply(
        self,
        queryset: QuerySet[ModelT],
        *,
        only: bool = False,
        rules: Mapping[str, ExpandRule] = MappingProxyType({}),
    ) -> QuerySet[ModelT]:
        if self.select or self.forced_select:
            queryset = queryset.select_related(*self.select, *self.forced_select)
        existing: tuple[str | AnyPrefetch, ...] = getattr(queryset, "_prefetch_related_lookups", ())
        seen = {item if isinstance(item, str) else item.prefetch_to for item in existing}
        lookups: list[str | AnyPrefetch] = []
        for lookup, plan in self.prefetch:
            if lookup in seen:
                continue  # the view prefetches it already, its way
            rule = rules.get(lookup)
            if rule is not None:
                base = plan.apply(plan.queryset(), only=only)
                lookups.append(Prefetch(lookup, queryset=_shaped(self.model, lookup, base, rule)))
            elif (
                plan.select
                or plan.forced_select
                or plan.prefetch
                or plan.forced_prefetch
                or (only and plan.is_exact())
            ):
                lookups.append(Prefetch(lookup, queryset=plan.apply(plan.queryset(), only=only)))
            else:
                lookups.append(lookup)
        for lookup in self.forced_prefetch:
            if lookup not in seen:
                lookups.append(lookup)
                seen.add(lookup)
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
        prefetch: list[str] = [*self.forced_prefetch]
        for lookup, plan in self.prefetch:
            prefetch.append(lookup)
            select, nested = plan.lookups()
            prefetch.extend(f"{lookup}__{name}" for name in (*select, *nested))
        return (*self.select, *self.forced_select), tuple(prefetch)


def requires_related(*lookups: str) -> Callable[[T], T]:
    """Declare relations a schema resolver reads, so the planner loads them explicitly.

    ``@requires_related("author", "comments__user")`` on a ``resolve_<field>`` method adds
    the lookups to the query plan even though a resolver body cannot be analysed.
    """

    def decorate(function: T) -> T:
        setattr(function, "__ninja_devx_related__", tuple(lookups))  # noqa: B010 - dunder name
        return function

    return decorate


def _hints(resolver: object) -> tuple[str, ...]:
    hints: tuple[str, ...] = getattr(resolver, "__ninja_devx_related__", ())
    if not hints:
        function = getattr(resolver, "__func__", None)
        hints = getattr(function, "__ninja_devx_related__", ())
    return hints


def _apply_hints(plan: QueryPlan, model: type[Model], hints: Sequence[str]) -> None:
    for lookup in hints:
        head = lookup.split("__", 1)[0]
        try:
            found = model._meta.get_field(head)
        except FieldDoesNotExist:
            continue  # ``ninja_devx.W006`` reports it
        forward = bool(
            getattr(found, "many_to_one", False) or getattr(found, "one_to_one", False)
        ) and bool(getattr(found, "concrete", False))
        target = plan.forced_select if forward else plan.forced_prefetch
        if lookup not in target:
            target.append(lookup)


def query_plan(
    model: type[Model],
    schema: type[BaseModel],
    expand: frozenset[str] = frozenset(),
    hints: Sequence[str] = (),
) -> QueryPlan:
    """The (cached) loading plan for serializing ``model`` instances with ``schema``.

    ``Expandable`` relations are loaded only when named in ``expand``. ``hints`` are extra
    ``select_related``/``prefetch_related`` lookups applied on top of the derived plan.
    """
    _cache: dict[
        tuple[type[Model], type[BaseModel], frozenset[str], tuple[str, ...]], QueryPlan
    ] = owned_cache(schema, "query_plans")
    key = (model, schema, expand, tuple(hints))
    if (cached := _cache.get(key)) is None:
        cached = _cache[key] = QueryPlan(model)
        _collect(cached, model, schema, "", depth=0, expand=expand)
        _apply_hints(cached, model, hints)
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
    hints: Sequence[str] = (),
    rules: Mapping[str, ExpandRule] = MappingProxyType({}),
) -> QuerySet[ModelT]:
    return query_plan(queryset.model, schema, expand, hints).apply(
        queryset, only=only, rules=_targeted(schema, rules)
    )


def resolve_expand_attribute(schema: type[BaseModel], name: str) -> str:
    """The model attribute an ``Expandable`` schema field ``name`` reads (its alias, if any)."""
    info = schema.model_fields[name]
    return info.alias if isinstance(info.alias, str) else name


def _targeted(schema: type[BaseModel], rules: Mapping[str, ExpandRule]) -> dict[str, ExpandRule]:
    return {
        resolve_expand_attribute(schema, name): rule
        for name, rule in rules.items()
        if name in schema.model_fields
    }


def expand_limit_target(model: type[Model], lookup: str) -> tuple[str, type[Model]] | None:
    """``(partition column, related model)`` for capping ``lookup`` per parent, or ``None``
    when ``lookup`` is not a plain reverse foreign key of ``model``."""
    try:
        relation = model._meta.get_field(lookup)
    except FieldDoesNotExist:
        return None
    remote = getattr(relation, "field", None)
    partition = getattr(remote, "attname", None)
    related = getattr(relation, "related_model", None)
    if (
        not isinstance(partition, str)
        or not isinstance(related, type)
        or not issubclass(related, Model)
    ):
        return None
    return partition, related


def _default_ordering(model: type[Model]) -> tuple[str, ...]:
    ordering = tuple(str(item) for item in model._meta.ordering or ())
    return ordering or ("-pk",)


def _limited(
    model: type[Model], lookup: str, queryset: QuerySet[Model], order_by: Sequence[str], limit: int
) -> QuerySet[Model]:
    target = expand_limit_target(model, lookup)
    if target is None:
        return queryset  # ``ninja_devx.W007`` reports it
    partition, related = target
    ordering = tuple(order_by) or _default_ordering(related)
    if _SUPPORTS_QUALIFY:
        ranked = queryset.annotate(
            **{
                _RANK_ANNOTATION: Window(
                    expression=RowNumber(), partition_by=F(partition), order_by=list(ordering)
                )
            }
        )
        return ranked.filter(**{f"{_RANK_ANNOTATION}__lte": limit}).order_by(*ordering)
    inner = (
        queryset.filter(**{partition: OuterRef(partition)})
        .order_by(*ordering)
        .values_list("pk", flat=True)[:limit]
    )
    return queryset.filter(pk__in=Subquery(inner)).order_by(*ordering)


def _shaped(
    model: type[Model], lookup: str, queryset: QuerySet[Model], rule: ExpandRule
) -> QuerySet[Model]:
    if rule.filter is not None:
        queryset = queryset.filter(rule.filter)
    if rule.order_by:
        queryset = queryset.order_by(*rule.order_by)
    if rule.limit is None:
        return queryset
    return _limited(model, lookup, queryset, rule.order_by, rule.limit)


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
            resolver = getattr(schema, f"resolve_{attribute}", None)
            _apply_hints(plan, model, _hints(resolver))
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
