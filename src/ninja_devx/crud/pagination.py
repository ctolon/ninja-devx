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
from typing import TYPE_CHECKING
from urllib import parse

from django.db.models import Model, QuerySet
from django.http import HttpRequest
from django.utils.translation import gettext as _
from ninja import Schema
from ninja.errors import ValidationError
from ninja.pagination import CursorPagination as NinjaCursorPagination
from ninja.pagination import LimitOffsetPagination as NinjaLimitOffsetPagination
from pydantic import Field

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

__all__ = ["CursorPagination", "LimitOffsetPagination"]


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
        return run(paginator, queryset, pagination, request, **params)

    async def apaginate_queryset(
        self,
        queryset: QuerySet[Model],
        pagination: NinjaCursorPagination.Input,
        request: HttpRequest,
        **params: object,
    ) -> object:
        paginator = self._for(queryset, pagination)
        run: Callable[..., Awaitable[object]] = vars(NinjaCursorPagination)["apaginate_queryset"]
        return await run(paginator, queryset, pagination, request, **params)

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
    """``?limit=&offset=`` pages with stable ordering, links and an optional count.

    ::

        class EventController(ReadOnlyModelController[Event, EventOut]):
            pagination_class = LimitOffsetPagination
            pagination_options = {"limit": 50, "max_limit": 200, "count": False}

    Compared with Ninja's ``LimitOffsetPagination``:

    - pages never overlap or skip rows: the primary key is added to the ordering;
    - ``next``/``previous`` links keep the other query parameters;
    - ``count=False`` skips the ``COUNT(*)`` query (``count`` is then ``null``) and
      fetches one extra row to know whether there is a next page;
    - ``limit`` above ``max_limit`` is clamped instead of rejected, and ``max_offset`` turns
      deep offsets (slow on large tables) into a 422 suggesting cursor pagination.
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
        count: bool = True,
        max_offset: int | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(max_limit=max_limit, **kwargs)
        self.default_limit = limit
        self.count = count
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
        total = ordered.count() if self.count else None
        return self._page(request, rows, limit, offset, total)

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
        total = await ordered.acount() if self.count else None
        return self._page(request, rows, limit, offset, total)

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
        request: HttpRequest, rows: list[Model], limit: int, offset: int, total: int | None
    ) -> dict[str, object]:
        has_next = len(rows) > limit
        url = request.build_absolute_uri()
        return {
            "items": rows[:limit],
            "count": total,
            "next": _with_query(url, limit=limit, offset=offset + limit) if has_next else None,
            "previous": (
                _with_query(url, limit=limit, offset=max(offset - limit, 0)) if offset else None
            ),
        }


def _with_query(url: str, **values: int) -> str:
    scheme, netloc, path, query, fragment = parse.urlsplit(url)
    pairs = [(key, value) for key, value in parse.parse_qsl(query) if key not in values]
    pairs += [(key, str(value)) for key, value in values.items()]
    return parse.urlunsplit((scheme, netloc, path, parse.urlencode(pairs), fragment))
