"""Pluggable full-text search for list endpoints.

Without a backend, ``search_fields`` become ``icontains`` lookups in the generated filter
schema. Set ``search_backend`` on a controller to replace that, for example with
PostgreSQL's ``SearchVector``; the search parameter stays documented either way::

    from ninja_devx.crud import PostgresSearch

    class ArticleController(CRUDController[Article, ArticleOut, ArticleIn]):
        search_fields = ("title", "body")
        search_backend = PostgresSearch(config="english")
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, TypeVar

from django.db.models import Model, Q, QuerySet

__all__ = ["IContainsSearch", "PostgresSearch", "SearchBackend"]

ModelT = TypeVar("ModelT", bound=Model)


class SearchBackend(Protocol[ModelT]):
    """Turns a search term and field names into a filtered queryset (lazy)."""

    def search(
        self, queryset: QuerySet[ModelT], term: str, fields: Sequence[str], /
    ) -> QuerySet[ModelT]: ...


class IContainsSearch:
    """Case-insensitive substring match across the fields, as a backend.

    Equivalent to the built-in behaviour; a starting point for custom backends.
    """

    def search(
        self, queryset: QuerySet[ModelT], term: str, fields: Sequence[str], /
    ) -> QuerySet[ModelT]:
        query = Q()
        for name in fields:
            query |= Q(**{f"{name}__icontains": term})
        return queryset.filter(query) if fields else queryset


class PostgresSearch:
    """PostgreSQL full-text search across the fields with a shared language config.

    The vector is computed per query. Large tables need a stored ``SearchVectorField``
    with a GIN index and a backend that filters on it.

    :param config: ``to_tsvector``/``to_tsquery`` language configuration.
    """

    def __init__(self, *, config: str = "english") -> None:
        self.config = config

    def search(
        self, queryset: QuerySet[ModelT], term: str, fields: Sequence[str], /
    ) -> QuerySet[ModelT]:
        from django.contrib.postgres.search import SearchQuery, SearchVector

        if not fields:
            return queryset
        vector = SearchVector(*fields, config=self.config)
        return queryset.annotate(_ndx_search=vector).filter(
            _ndx_search=SearchQuery(term, config=self.config)
        )
