# HTTP caching and optimistic locking

!!! tip "Reference"

    Every option on this page, with types and defaults: [configuration reference](../options/crud.md#etag).

Conditional requests (RFC 9110) come with one attribute:

```python
class PostController(CRUDController[Post, PostOut, PostIn]):
    etag = ETag()
```

| Request | Behavior |
|---|---|
| `GET /{pk}` | sends `ETag`; `If-None-Match` with the current tag → `304 Not Modified`, before the body is serialized |
| `GET /` | sends an `ETag` of the rendered page; `If-None-Match` → `304` |
| `PUT` / `PATCH` / `DELETE` | `If-Match` with a stale tag → `412 Precondition Failed`, nothing is written; success sends the new `ETag` |

Clients get lost-update protection by echoing the tag they read:

```text
GET /posts/7                         → 200, ETag: "8f1c…"
PATCH /posts/7   If-Match: "8f1c…" → 200, ETag: "b02e…"
PATCH /posts/7   If-Match: "8f1c…" → 412 {"code": "precondition_failed", "etag": "\"b02e…\""}
```

## Options

```python
etag = ETag(field="updated_at")  # tag from a version field: no extra serialization
etag = ETag(require_if_match=True)  # writes without If-Match → 428 Precondition Required
etag = ETag(weak=True)  # optional weak tags for read caching only
etag = ETag(lists=False)  # don't tag list responses
```

- **Without `field`**, the tag hashes the output schema's representation. Any visible
  change changes it, at the cost of one extra serialization when checking.
- **With `field`**, the tag comes from the primary key and that field. Use an `auto_now`
  timestamp or a version counter that every write updates.

Tags are strong by default. `If-Match` uses strong comparison: a weak tag cannot
satisfy it (except the existence wildcard `*`). `If-None-Match` uses weak comparison.
The representation hash uses the current request's field visibility and response shape.

Built-in writes hold a write-database transaction and row lock from version lookup
through save/delete. Use a database with row locks (such as PostgreSQL) for concurrent
optimistic locking; SQLite does not provide equivalent row-lock guarantees. Custom
write operations must hold their own transaction and lock when using these helpers.

412 and 428 are documented in OpenAPI for the write operations. `If-Match: *` always
passes.

## Other operations

```python
@get("/stats", decorators=[conditional()])
def stats(self, request: HttpRequest) -> Stats: ...
```

`conditional()` tags any successful GET from its rendered body. It uses Ninja's
`decorate_view`, so it works on sync and async operations. For your own detail
operations, call `self.conditional_object(request, obj)`, `self.check_preconditions(request, obj)`
and `self.written(request, obj)`, as the generic ones do.
