# CRUD

!!! tip "Reference"

    Every option on this page, with types and defaults: [configuration reference](../options/crud.md).

The generic arguments are the configuration; type checkers see them and so does Ninja.

| Class | Routes |
|---|---|
| `ListMixin[M, Out]` | `GET /` |
| `RetrieveMixin[M, Out]` | `GET /{pk}` |
| `CreateMixin[M, Out, In]` | `POST /` → 201 |
| `UpdateMixin[M, Out, In]` | `PUT /{pk}`, `PATCH /{pk}` (sent fields only) |
| `DestroyMixin[M]` | `DELETE /{pk}` → 204 |
| `ReadOnlyModelController[M, Out]` | list + retrieve |
| `CRUDController[M, Out, In]` | all of the above |
| `AsyncReadOnlyModelController`, `AsyncCRUDController` | the same with `mode = "async"` |

## Schemas generated from the model

For internal tools, admin APIs and prototypes, skip writing schemas. Ninja's
`create_schema` builds them from the model:

```python
from ninja_devx.crud import AutoCRUDController


class TagController(AutoCRUDController[Tag]):
    schema_exclude = ("search_vector",)
    read_only_fields = ("created_by",)  # in responses, not accepted in requests
    write_only_fields = ("secret",)  # accepted, never returned
```

- The output schema is `TagOut` and the input schema is `TagIn` (OpenAPI component
  names). The primary key and non-editable fields (`auto_now`) are read-only
  automatically.
- Misspelled field names fail at startup with a suggestion.
- Also available: `AutoReadOnlyController`, `AsyncAutoCRUDController`,
  `AsyncAutoReadOnlyController`, and `model_schemas(model, ...)` to get the pair yourself.
- Everything else (filters, permissions, tenancy, pagination) works as on `CRUDController`.

Generated schemas carry no validation beyond the model's field types. When the API is
public, generate explicit ones with [`devx_scaffold`](codegen.md) and edit them.

## Sync and async

Model controllers implement every operation both sync and async. `mode` (`"auto"` by
default, following `NINJA_DEVX["ASYNC_MODE"]`) picks the one to register. See
[Async and sync](async.md).

## Hooks

Every hook receives `request`:

- **Reading:** `get_queryset(request)`, `scoped_queryset(request)`, `get_object(request, pk)`.
- **Writing:** `perform_create(request, payload)`, `perform_update(request, instance, data)`,
  `perform_destroy(request, instance)`. By default they call the service from
  `get_service(request)` ([Services](layers.md#services-behind-crud)).
- **Context:** `context_data(request)` returns the fields set from the request on create:
  the owner and the parent.

`scoped_queryset` applies, in order: `get_queryset`, the parent, the owner, soft delete, and
the N+1 optimizations. Override `get_queryset` for your own scoping.

## Class attributes

| Attribute | Default | Effect |
|---|---|---|
| `lookup_field` | `"pk"` | model field matched by the lookup segment |
| `lookup_param` | `"pk"` | name of the path segment: `"slug"` exposes `/{slug}` |
| `lookup_converter` | `False` | Django path converters (`{int:pk}`): invalid ids get Django's 404 instead of a JSON 422 |
| `routes` | `{}` | per-operation overrides: `{"destroy": {"enabled": False}, "restore": {"path": "/{pk}/undelete"}}` |
| `tenant_field` | `None` | scope everything to the current tenant ([Multi-tenancy](tenancy.md)) |
| `etag` | `None` | `ETag()`: 304 on reads, `If-Match` + 412 on writes ([HTTP caching](conditional.md)) |
| `owner_field` | `None` | set on create; adds `IsAuthenticated` + `IsOwner`; `"project__owner"` follows relations |
| `scope_queryset_to_owner` | `False` | also restrict every query to the user's objects |
| `parent` | `None` | `Parent(Model, field=...)` for nested routes |
| `search_fields` | `()` | `?search=` over `icontains` lookups |
| `filter_fields` | `{}` | `{"status": ("exact",), "created": ("gte", "lte"), "id": ("in",)}` |
| `filter_schema` | `None` | an explicit `FilterSchema` instead |
| `ordering_fields` / `default_ordering` | `()` | `?ordering=-created`, validated against an enum |
| `search_param` / `ordering_param` | `"search"` / `"ordering"` | query parameter names |
| `pagination_class` | settings | a Ninja pagination class, or `CursorPagination` |
| `pagination_options` | `{}` | arguments for the pagination class: `{"page_size": 50}` |
| `validate_model` | `True` | `full_clean()`; errors are 422 |
| `refresh_after_write` | `True` | reload through `scoped_queryset` after writes |
| `optimize_queries` | `True` | derive `select_related`/`prefetch_related` from `Out`; `"only"` also restricts columns |
| `service_class` | `None` | a `ModelService` subclass for writes, resolved from the container |
| `selector_class` | `None` | a selector replacing `get_queryset` for lists |
| `mode` | `"auto"` | `"sync"`, `"async"` or `"auto"` |
| `bulk_limit` | settings | objects per bulk request |

Unset attributes fall back to [settings](settings.md).

Generated filters use the model's field types. Choices become enums, `in` takes repeated
query parameters, and unknown fields or lookups are startup errors.

## Custom actions

```python
@post("/{pk}/publish", response=PostOut)
def publish(self, request: HttpRequest, post: Instance[Post]) -> Post: ...
```

`Instance[M]` exposes a `pk` path parameter and loads the object, returning 404 when it is
missing and applying object permissions. For another model it uses that model's manager.
`Locked[M]` loads it with `select_for_update()` (use it with `atomic=True`).
`pk: Lookup` gives the raw typed value.

Operation permissions replace the controller's. To add to them instead, use
`permissions=Also(IsStaff())`. With `owner_field`, that keeps the owner check.

## N+1 queries

Output schemas describe what gets serialized, so the queries are derived from them:

- A forward foreign key rendered as a nested schema is joined (`select_related`).
- Many-to-many and reverse relations are prefetched with a `Prefetch` whose queryset
  joins their own nested foreign keys. `comments: list[CommentOut]`, where `CommentOut`
  nests `author`, costs one query for the comments and their authors.
- Lookups the view already prefetches (in `get_queryset`) are left alone.
- `optimize_queries = "only"` also selects just the columns the schema reads. It is
  skipped automatically when the schema reads anything that is not a model field
  (resolvers, properties).

`optimize_queryset(queryset, Schema)` and `query_plan(Model, Schema)` are available for
your own views. Check the result with `assert_max_queries(n)` or pytest-django's
`django_assert_max_num_queries`.

A resolver body cannot be analysed statically, so declare what it reads. The planner
loads these explicitly and the `ninja_devx.W006` system check validates them:

```python
from ninja_devx.crud import requires_related


class ArticleOut(Schema):
    id: int
    author_name: str

    @staticmethod
    @requires_related("author")
    def resolve_author_name(obj: Article) -> str:
        return obj.author.username


class ArticleController(ReadOnlyModelController[Article, ArticleOut]):
    related = ("author", "comments__user")  # always loaded, for any custom resolver
```

`related` takes `select_related` paths for forward keys and `prefetch_related` paths for
many/many-to-many relations; both forms accept `__` chains.

## Search backends

`search_fields` uses `icontains` by default. Set `search_backend` to change how the
`search` parameter filters the queryset, for example PostgreSQL full-text search:

```python
from ninja_devx.crud import PostgresSearch

class ArticleController(ReadOnlyModelController[Article, ArticleOut]):
    search_fields = ("title", "body")
    search_backend = PostgresSearch(config="english")
```

Implement `SearchBackend` (a `search(queryset, term, fields)` method) for your own index.
The `search` parameter stays in the generated filter schema and in OpenAPI; with a backend
the controller passes the term to `search()` instead of applying `icontains` lookups.
`PostgresSearch` computes the search vector per row; add a `SearchVectorField` with a GIN
index for production tables.

## Nested resources

```python
class CommentController(CRUDController[Comment, CommentOut, CommentIn]):
    parent = Parent(Post, field="post")


api.add_router("/posts/{post_pk}/comments", CommentController.as_router())
```

Every operation 404s for an unknown post, lists only that post's comments and assigns the
post on create. `get_parent(request)` returns it.

## Configurable routes

Every generic operation can be renamed, reconfigured or removed without overriding it:

```python
class PostController(CRUDController[Post, PostOut, PostIn]):
    routes = {
        "destroy": {"enabled": False},
        "partial_update": {"path": "/{pk}/edit", "summary": "Edit a post"},
        "list": {"throttle": [ScopedRateThrottle("search")]},
    }
```

Keys are operation names (`list`, `retrieve`, `create`, `update`, `partial_update`,
`destroy`, `restore`, `bulk_create`...), and values take any operation option plus `path`
and `enabled`. A misspelled name fails at startup and lists the available ones.
`routes` works on any `Controller`.

## Timestamps, user stamps and soft-delete columns

`ninja_devx.models` ships abstract bases for the bookkeeping columns most tables carry.
Combine them with a model; the controllers fill the user columns from the request:

```python
from ninja_devx.models import SoftDeletable, Stamped

class Post(Stamped, SoftDeletable):
    title = models.CharField(max_length=200)


class PostController(SoftDeleteMixin[Post, PostOut], CRUDController[Post, PostOut, PostIn]):
    pass
```

| Base | Columns | Filled by |
|---|---|---|
| `TimeStamped` | `created_at` (indexed), `updated_at` | Django (`auto_now_add`/`auto_now`) |
| `UserStamped` | `created_by`, `updated_by` | create and update operations, including bulk and import |
| `Stamped` | both of the above | |
| `SoftDeletable` | `deleted_at`, `deleted_by` | `SoftDeleteMixin`, with no `soft_delete` configuration |

The user columns are nullable: an anonymous write or a deleted user leaves `None`. All
columns are `editable=False`, so generated input schemas, `devx_scaffold` and the drift check
leave them out while output schemas may include them. `perform_create` uses `context_data`
and `perform_update` uses `update_context_data` for the stamps; an override that bypasses the
service must merge them itself. `ETag(field="updated_at")` pairs well with `TimeStamped`.

## Soft delete

```python
class PostController(SoftDeleteMixin[Post, PostOut], CRUDController[Post, PostOut, PostIn]):
    soft_delete = SoftDelete("deleted_at")  # nullable DateTimeField: now / None
    soft_delete = SoftDelete("is_deleted")  # BooleanField: True / False
    soft_delete = SoftDelete("is_active", deleted=False, active=True)
    soft_delete = SoftDelete("status", deleted="archived", active="published")
    soft_delete = SoftDelete("is_deleted", deleted_at="removed_on", deleted_by="removed_by")
```

- List `SoftDeleteMixin` first. It hides deleted rows everywhere and adds
  `POST /{pk}/restore`; rename or disable it with `routes`.
- `deleted` may be a callable (`timezone.now`).
- `deleted_at` and `deleted_by` record when and by whom.
- `queryset_with_deleted(request)` includes deleted rows (tenant, parent and owner still
  apply). `perform_restore` is overridable.
- The configuration is validated at startup.

### Cascade

Mark related objects deleted/restored with the parent. Only the marker field is cascaded:

```python
class PostController(SoftDeleteMixin[Post, PostOut], CRUDController[Post, PostOut, PostIn]):
    soft_delete = SoftDelete("deleted_at")
    soft_delete_cascade = ("comments", "attachments")
```

### Unique values after deletion

A normal unique constraint would keep a deleted row's value reserved forever. Use
`soft_delete_unique` in the model so uniqueness only applies to active rows:

```python
from django.db import models
from ninja_devx.crud import soft_delete_unique


class Post(models.Model):
    slug = models.SlugField()
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [soft_delete_unique(Post, "slug")]
```

It builds a partial `UniqueConstraint` with the active condition; pass
`config=SoftDelete(...)` when the marker is not `deleted_at`, and `name=` to override the
generated constraint name.

## Offset pagination

```python
from ninja_devx.crud import LimitOffsetPagination


class ProductController(ReadOnlyModelController[Product, ProductOut]):
    pagination_class = LimitOffsetPagination
    pagination_options = {"limit": 50, "max_limit": 200, "count": True, "max_offset": 10_000}
```

`GET /products/?limit=50&offset=100` returns:

```json
{"items": [...], "count": 1234, "next": "https://api.example.com/products/?limit=50&offset=150", "previous": "https://api.example.com/products/?limit=50&offset=50"}
```

- `next` and `previous` are absolute links that keep the other query parameters
  (filters, ordering).
- Pages are fetched with `limit + 1` rows, so `next` needs no count. `count=False` skips
  `COUNT(*)` on large tables and returns `"count": null`.
- `limit` above `max_limit` is clamped. `max_offset` rejects deeper pages with 422,
  because deep offsets get slower as the table grows; use cursor pagination for feeds
  and exports.
- The primary key is added to the ordering, so pages never overlap or skip rows.

## Cursor pagination

```python
class EventController(ReadOnlyModelController[Event, EventOut]):
    pagination_class = CursorPagination
    pagination_options = {"page_size": 50}
    ordering_fields = ("created", "priority")
    default_ordering = ("-created",)
```

`ninja_devx.crud.CursorPagination` builds on Ninja's `CursorPagination` and follows each
request's ordering:

- The ordering is `?ordering=` when given, else `default_ordering`, else the model's
  `Meta.ordering`, else `-pk`. The primary key is added as a tiebreaker.
- A cursor from one ordering is rejected (422) under another.
- Ordering fields must not be nullable, which is checked at startup.
- Pages cost one query and no `COUNT`, with sync and async support.

## Bulk operations

`BulkCreateMixin` (`POST /bulk`), `BulkUpdateMixin` (`POST /bulk-update` with
`{"pks": [...], "data": {...}}`) and `BulkDestroyMixin` (`POST /bulk-delete`).

- Each request runs in one transaction.
- Validation, object permissions and `perform_*` run per object.
- Requests are capped at `bulk_limit` objects.

### Partial success

`bulk_partial = True` on `BulkCreateMixin` or `BulkUpdateMixin` runs each item in its own
savepoint instead of the whole request:

```python
class ContactController(
    BulkCreateMixin[Contact, ContactOut, ContactIn],
    CRUDController[Contact, ContactOut, ContactIn],
):
    bulk_partial = True
```

- The response is 207 with `{"results": [{"index": 0, "status": 201, "data": {...}}, {"index": 1, "status": 422, "errors": [...]}]}`,
  one entry per input item, in order. A succeeding item's `errors` is absent; a failing
  item's `data` is absent.
- `errors` uses Ninja's validation error shape (`{"type", "loc", "msg"}`). Model validation
  failures and unknown primary keys in `bulk_update` (404) become entries instead of
  aborting the request; anything else still aborts it.
- The `X-Bulk-Failed` response header carries the number of failed entries.
- The default (`bulk_partial = False`) keeps the all-or-nothing behaviour above.

## Import and export

```python
from ninja_devx.crud.transfer import ExportMixin, ImportMixin


class ContactController(
    ExportMixin[Contact, ContactOut],
    ImportMixin[Contact, ContactIn],
    CRUDController[Contact, ContactOut, ContactIn],
):
    export_formats = ("csv", "jsonl")
    max_import_rows = 5_000
```

**`GET /export?format=csv`** (or `jsonl`) streams every object that `GET /` would return,
with the same filters, ordering, tenant/owner scoping, object permissions and field
visibility, but without pagination.

- The response is sent as it is produced (`StreamingHttpResponse`) and rows are read in
  chunks. Memory stays flat for large tables, in sync and async mode.
- CSV nests objects as dotted columns (`author.name`) and writes lists as JSON.
- Text cells starting with `=`, `+`, `-` or `@` are prefixed with `'` so spreadsheets
  don't run them as formulas (`csv_escape_formulas`).

**`POST /import`** takes a `multipart/form-data` `file` (CSV with a header row, or JSON
Lines):

- Every row is validated with the input schema and created through `perform_create`, so
  owners, tenants, services, audit logging and model validation apply.
- All rows are imported, or none. Errors are 422 in Ninja's format, with the file row:
  `{"loc": ["file", 3, "priority"], "msg": "Input should be a valid integer"}`.
- `?dry_run=true` validates everything and rolls back (200 with `created`).
- Files over `max_import_rows` are rejected with 413. Empty CSV cells use the schema
  default. Cells starting with `[` or `{` are parsed as JSON (many-to-many ids).

## Services and selectors

```python
class PostController(CRUDController[Post, PostOut, PostIn]):
    service_class = PostService  # writes
    selector_class = PublishedPosts  # GET /
```

See [Services, repositories and other layers](layers.md).

## Schema drift

`manage.py devx_scaffold --check` reports schemas that no longer match their models. See
[System checks](checks.md).

## Bulk and import input limits

Bulk update/delete reject duplicate primary keys with 422 before changing any rows.
Import accepts UTF-8 CSV and JSONL. Invalid encoding/parser input returns 422; files
above `max_import_bytes` (10,000,000 bytes by default) return 413 before parsing.
`max_import_rows` remains a separate limit. Dry-run performs writes inside a rolled-back
transaction; custom hooks must defer external work until commit to avoid side effects.

## Persistence and database boundaries

Built-in create, update, delete, bulk and import operations own a transaction on
`write_database(request)`: the operation/controller `database` option takes precedence,
then an explicit `get_queryset().using(alias)`, then Django's write router. Scoped reads,
row locks, the default repository, post-write checks and audit entries use that alias.
Import dry runs roll back that same transaction. A custom `ModelRepository` with a
conflicting alias raises `ControllerConfigError` before its write begins.

Every saved result must still exist in `scoped_queryset(request)` and pass object
permissions. A missing result raises 404 inside the transaction, rolling back the write
and its audit entries. This includes custom queryset filters: a published-only controller
cannot create an unpublished record. Use a controller whose write scope admits the
intended result. `refresh_after_write=False` preserves the returned instance but still
loads the persisted row for scope and object-permission verification.

Custom `perform_create`, `perform_update`, `perform_destroy` and service implementations
are trusted application code. Use `context_data(request)` for owner/tenant/parent fields,
`write_database(request)` for ORM writes, and return the saved model instance. Overriding
`refresh`, `scoped_queryset` or permission hooks can intentionally replace these checks.
The framework cannot roll back writes to another database, a custom external repository,
a storage service or an HTTP API. Schedule external effects with
`transaction.on_commit(callback, using=self.write_database(request))` or use an outbox.
There is no distributed transaction across aliases or external services.
