"""Pagination built on Ninja's paginators: cursor and limit/offset.

``CursorPagination`` is the cursor paginator that follows the list's ordering.

Ninja's ``CursorPagination`` orders by a fixed tuple chosen when the view is decorated.
This subclass takes the ordering of each request instead (``?ordering=`` restricted to
``ordering_fields``, else ``default_ordering``, else the model's ``Meta.ordering``, else
``-pk``), adds the primary key as a tiebreaker, and rejects a cursor created for a
different ordering::

    class EventController(ReadOnlyModelController[Event, EventOut]):
        pagination_class = CursorPagination
        pagination_options = {"page_size": 50}
        ordering_fields = ("created", "priority")
        default_ordering = ("-created",)

Cursors compare the first ordering field, so ordering fields must not be nullable; that is
checked at startup.
"""

from __future__ import annotations

import copy
import hashlib
from typing import TYPE_CHECKING, Literal, TypeAlias, TypeVar, cast
from urllib import parse

from asgiref.sync import sync_to_async
from django.db import connections
from django.db.models import Model, QuerySet
from django.http import HttpRequest
from django.utils.translation import gettext as _
from ninja import Schema
from ninja.errors import ValidationError
from ninja.pagination import CursorPagination as NinjaCursorPagination
from ninja.pagination import LimitOffsetPagination as NinjaLimitOffsetPagination
from pydantic import Field

from ..http.pagination_headers import PAGINATION_ATTR

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

__all__ = ["CursorPagination", "LimitOffsetPagination"]

PageT = TypeVar("PageT")
_CountOption: TypeAlias = bool | Literal["estimate"] | int


class CursorPagination(NinjaCursorPagination):
    class Cursor(NinjaCursorPagination.Cursor):
        k: str | None = None
        """Fingerprint of the ordering the cursor was created for."""

    _signature: str | None = None

    def paginate_queryset(
        self,
        queryset: QuerySet[Model],
        pagination: NinjaCursorPagination.Input,
        request: HttpRequest,
        **params: object,
    ) -> object:
        paginator = self._for(queryset, pagination)
        run: Callable[..., object] = vars(NinjaCursorPagination)["paginate_queryset"]
        return _record(request, run(paginator, queryset, pagination, request, **params))

    async def apaginate_queryset(
        self,
        queryset: QuerySet[Model],
        pagination: NinjaCursorPagination.Input,
        request: HttpRequest,
        **params: object,
    ) -> object:
        paginator = self._for(queryset, pagination)
        run: Callable[..., Awaitable[object]] = vars(NinjaCursorPagination)["apaginate_queryset"]
        return _record(request, await run(paginator, queryset, pagination, request, **params))

    def _for(
        self, queryset: QuerySet[Model], pagination: NinjaCursorPagination.Input
    ) -> CursorPagination:
        ordering = _ordering_of(queryset)
        signature = hashlib.blake2b(",".join(ordering).encode(), digest_size=4).hexdigest()
        cursor = self.Cursor.from_encoded_param(pagination.cursor)
        key: object = getattr(cursor, "k", None)
        if key is not None and key != signature:
            raise ValidationError(
                [
                    {
                        "type": "cursor",
                        "loc": ["query", "cursor"],
                        "msg": _(
                            "The cursor belongs to a different ordering; start from the first page"
                        ),
                    }
                ]
            )
        paginator = copy.copy(self)  # the decorated view shares one instance between requests
        paginator.ordering = ordering
        first = ordering[0]
        paginator._order_attribute = first.removeprefix("-")
        paginator._order_attribute_reversed = first.startswith("-")
        paginator._signature = signature
        return paginator

    def _build_next_cursor(
        self,
        current_cursor: NinjaCursorPagination.Cursor,
        results: list[object],
        additional_position: str | None = None,
    ) -> NinjaCursorPagination.Cursor | None:
        built = super()._build_next_cursor(current_cursor, results, additional_position)
        return self._signed(built)

    def _build_previous_cursor(
        self,
        current_cursor: NinjaCursorPagination.Cursor,
        results: list[object],
        additional_position: str | None = None,
    ) -> NinjaCursorPagination.Cursor | None:
        built = super()._build_previous_cursor(current_cursor, results, additional_position)
        return self._signed(built)

    def _signed(
        self, cursor: NinjaCursorPagination.Cursor | None
    ) -> NinjaCursorPagination.Cursor | None:
        if cursor is None:
            return None
        return self.Cursor(p=cursor.p, r=cursor.r, o=cursor.o, k=self._signature)


def _ordering_of(queryset: QuerySet[Model], *, default: str = "-pk") -> tuple[str, ...]:
    declared: Sequence[object] = tuple(queryset.query.order_by) or tuple(
        queryset.model._meta.ordering or ()
    )
    ordering = [item for item in declared if isinstance(item, str) and item != "?"]
    if not ordering:
        ordering = [default]
    names = {item.removeprefix("-") for item in ordering}
    pk_name = queryset.model._meta.pk.name
    if not names & {"pk", pk_name}:
        ordering.append("-pk" if ordering[0].startswith("-") else "pk")
    return tuple(ordering)


class LimitOffsetPagination(NinjaLimitOffsetPagination):
    """``?limit=&offset=`` pages with stable ordering, links and a configurable count.

    ::

        class EventController(ReadOnlyModelController[Event, EventOut]):
            pagination_class = LimitOffsetPagination
            pagination_options = {"limit": 50, "max_limit": 200, "count": False}

    Compared with Ninja's ``LimitOffsetPagination``:

    - pages never overlap or skip rows: the primary key is added to the ordering;
    - ``next``/``previous`` links keep the other query parameters;
    - ``limit`` above ``max_limit`` is clamped instead of rejected, and ``max_offset`` turns
      deep offsets (slow on large tables) into a 422 suggesting cursor pagination.

    ``count`` picks how the total is produced (the response body's ``count`` is always a
    plain int or ``null``):

    - ``True`` (default): an exact ``COUNT(*)``.
    - ``False``: no count query (``count`` is ``null``); one extra row is fetched instead,
      to know whether there is a next page.
    - ``"estimate"``: on PostgreSQL, ``pg_class.reltuples`` (instant, approximate) for an
      unfiltered queryset; an exact count otherwise, and on every other database.
    - an integer ``N``: exact while there are at most ``N`` rows; beyond that, ``count`` is
      ``N`` and ``PaginationHeadersMiddleware`` sends ``X-Total-Count: N+`` instead of ``N``.
    """

    class Input(Schema):  # pyright: ignore[reportIncompatibleVariableOverride]
        limit: int | None = Field(None, ge=1, description="Items per page.")
        offset: int = Field(0, ge=0, description="Items to skip.")

    class Output(Schema):  # pyright: ignore[reportIncompatibleVariableOverride]
        items: list[object]
        count: int | None
        next: str | None
        previous: str | None

    def __init__(
        self,
        *,
        limit: int = 100,
        max_limit: int = 1000,
        count: _CountOption = True,
        max_offset: int | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(max_limit=max_limit, **kwargs)
        self.default_limit = limit
        self.count: _CountOption = count
        self.max_offset = max_offset

    def paginate_queryset(
        self,
        queryset: QuerySet[Model],
        pagination: NinjaLimitOffsetPagination.Input,
        request: HttpRequest,
        **params: object,
    ) -> object:
        limit, offset = self._window(pagination)
        ordered = self._ordered(queryset)
        rows = list(ordered[offset : offset + limit + 1])
        total, lower_bound = _total(self.count, queryset)
        return self._page(request, rows, limit, offset, total, lower_bound)

    async def apaginate_queryset(
        self,
        queryset: QuerySet[Model],
        pagination: NinjaLimitOffsetPagination.Input,
        request: HttpRequest,
        **params: object,
    ) -> object:
        limit, offset = self._window(pagination)
        ordered = self._ordered(queryset)
        rows = [row async for row in ordered[offset : offset + limit + 1]]
        total, lower_bound = await _atotal(self.count, queryset)
        return self._page(request, rows, limit, offset, total, lower_bound)

    def _window(self, pagination: NinjaLimitOffsetPagination.Input) -> tuple[int, int]:
        requested: object = getattr(pagination, "limit", None)
        limit = requested if isinstance(requested, int) else self.default_limit
        offset: object = getattr(pagination, "offset", 0)
        start = offset if isinstance(offset, int) else 0
        if self.max_offset is not None and start > self.max_offset:
            raise ValidationError(
                [
                    {
                        "type": "offset",
                        "loc": ["query", "offset"],
                        "msg": _(
                            "offset is limited to %(max)d; use cursor pagination to walk further"
                        )
                        % {"max": self.max_offset},
                    }
                ]
            )
        return min(limit, int(self.max_limit)), start

    @staticmethod
    def _ordered(queryset: QuerySet[Model]) -> QuerySet[Model]:
        return queryset.order_by(*_ordering_of(queryset, default="pk"))

    @staticmethod
    def _page(
        request: HttpRequest,
        rows: list[Model],
        limit: int,
        offset: int,
        total: int | None,
        lower_bound: bool = False,
    ) -> dict[str, object]:
        has_next = len(rows) > limit
        url = request.build_absolute_uri()
        next_url = _with_query(url, limit=limit, offset=offset + limit) if has_next else None
        previous_url = (
            _with_query(url, limit=limit, offset=max(offset - limit, 0)) if offset else None
        )
        page: dict[str, object] = {
            "items": rows[:limit],
            "count": total,
            "next": next_url,
            "previous": previous_url,
        }
        return _record(request, page, count_lower_bound=lower_bound)


def _unfiltered(queryset: QuerySet[Model]) -> bool:
    query = queryset.query
    return not query.where.children and query.combinator is None


def _reltuples_estimate(queryset: QuerySet[Model]) -> int | None:
    connection = connections[queryset.db]
    if connection.vendor != "postgresql":
        return None
    table = connection.ops.quote_name(queryset.model._meta.db_table)
    with connection.cursor() as cursor:
        # to_regclass resolves the quoted, schema-qualified name and returns NULL for a
        # missing table, instead of matching a same-named table in another schema.
        cursor.execute("SELECT reltuples FROM pg_class WHERE oid = to_regclass(%s)", [table])
        row = cast("tuple[float | None] | None", cursor.fetchone())
    if row is None or row[0] is None or row[0] < 0:
        return None
    return int(row[0])


def _total(count: _CountOption, queryset: QuerySet[Model]) -> tuple[int | None, bool]:
    """``(count, lower_bound)`` for the ``count`` pagination option."""
    if count is False:
        return None, False
    if count is True:
        return queryset.count(), False
    if count == "estimate":
        if _unfiltered(queryset) and (estimate := _reltuples_estimate(queryset)) is not None:
            return estimate, False
        return queryset.count(), False
    found = queryset.order_by()[: count + 1].count()
    lower_bound = found > count
    return (count, True) if lower_bound else (found, False)


async def _atotal(count: _CountOption, queryset: QuerySet[Model]) -> tuple[int | None, bool]:
    if count is False:
        return None, False
    if count is True:
        return await queryset.acount(), False
    if count == "estimate":
        if _unfiltered(queryset):
            estimate = await sync_to_async(_reltuples_estimate)(queryset)
            if estimate is not None:
                return estimate, False
        return await queryset.acount(), False
    found = await queryset.order_by()[: count + 1].acount()
    lower_bound = found > count
    return (count, True) if lower_bound else (found, False)


def _record(request: HttpRequest, page: PageT, *, count_lower_bound: bool = False) -> PageT:
    """Keep ``count``/``next``/``previous`` on the request for the pagination headers."""
    result: object = page
    if isinstance(result, dict):
        values = cast("dict[str, object]", result)
        request.__dict__[PAGINATION_ATTR] = {
            **{key: values.get(key) for key in ("count", "next", "previous")},
            "count_lower_bound": count_lower_bound,
        }
    return page


def _with_query(url: str, **values: int) -> str:
    scheme, netloc, path, query, fragment = parse.urlsplit(url)
    pairs = [(key, value) for key, value in parse.parse_qsl(query) if key not in values]
    pairs += [(key, str(value)) for key, value in values.items()]
    return parse.urlunsplit((scheme, netloc, path, parse.urlencode(pairs), fragment))
