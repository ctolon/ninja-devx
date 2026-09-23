# Query tuning

Three ways to shape how list endpoints hit the database once the [N+1 planner](crud.md#n1-queries)
and [`related` hints](crud.md#n1-queries) already pick the base `select_related`/`prefetch_related`.

## Expand rules

`?expand=comments` prefetches every row of an `Expandable` relation. `expand_rules` filters,
orders and caps how many are loaded per parent:

```python
from django.db.models import Q
from ninja_devx.crud.optimization import ExpandRule


class ArticleController(ReadOnlyModelController[Article, ArticleOut]):
    expand_rules = {
        "comments": ExpandRule(filter=Q(published=True), order_by=("-created",), limit=5),
    }
```

- Keys must name an `Expandable` field of the output schema; an unknown key is a
  `ControllerConfigError` at startup.
- `filter` and `order_by` are plain queryset operations, applied before `limit`.
- `limit` ranks rows per parent with a window function (a `QUALIFY`-style filter on
  Django 5.0+) or, on Django 4.2, an equivalent correlated subquery, and is still loaded
  through a single `Prefetch` query — no N+1.
- `limit` only works on a plain reverse foreign key (`comments` above). A many-to-many or
  similar relation is prefetched unlimited instead, with `ninja_devx.W007` at startup.
- Works for both sync and async lists, and for retrieve.

## Query diagnostics headers

`QueryExplainMiddleware` adds `X-Query-Count`, `X-Query-Time` (milliseconds) and
`X-Query-Plan` to responses, for local development:

```python
from ninja_devx.http.explain import QueryExplainMiddleware

use_middleware(api, QueryExplainMiddleware())             # active only when DEBUG
use_middleware(api, QueryExplainMiddleware(enabled=True))  # a staff-only diagnostics API
```

- Active when `settings.DEBUG` is true, or always with `enabled=True`; every hook is a
  no-op otherwise, so nothing is captured or added in production by default.
- `X-Query-Plan` lists the `select_related`/`prefetch_related` lookups the N+1 planner
  chose for the request (`"select_related=author; prefetch_related=comments"`); it is
  omitted when the schema has no relations to load.
- SQL text never reaches a header, only counts, timings and lookup names.
- Database connections are thread-local: an async list whose ORM access hops threads
  (a plain `sync_to_async` fallback, without an async paginator) can undercount, since
  those queries run on a connection this middleware never touched.

## Pagination count strategy

`LimitOffsetPagination`'s `count` option picks how the total is produced. The response
body's `count` is always a plain int or `null`:

```python
class ProductController(ReadOnlyModelController[Product, ProductOut]):
    pagination_class = LimitOffsetPagination
    pagination_options = {"count": 10_000}
```

- `True` (default): an exact `COUNT(*)`.
- `False`: no count query; `count` is `null` (one extra row is fetched instead, to know
  whether there is a next page).
- `"estimate"`: on PostgreSQL, `pg_class.reltuples` (instant, approximate) for an
  unfiltered queryset; an exact count otherwise, and on every other database.
- an integer `N`, as above: exact while there are at most `N` rows; beyond that, `count`
  is `N` and `PaginationHeadersMiddleware` sends `X-Total-Count: N+` instead of `N`.
- Works for both sync and async lists. See [Offset pagination](crud.md#offset-pagination)
  for the rest of `LimitOffsetPagination`.
