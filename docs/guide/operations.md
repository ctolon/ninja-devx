# Operations

!!! tip "Reference"

    Every option on this page, with types and defaults: throttle storage on the
    [tenancy, caching and throttling reference](../options/http.md), the request log on the
    [middleware reference](../options/middleware.md), `Sensitive` on the
    [permissions reference](../options/permissions.md), and Redis throttle storage on the
    [contrib reference](../options/contrib.md).

Running an API in production: exact rate limits under load, one log line per request for
your aggregator, and a way to stop secrets from leaking into logs, error bodies and exports.

## Atomic throttle storage

[Throttles](throttling.md) count hits through a `ThrottleStorage`, not the cache directly:

```python
from ninja_devx.http.throttling import ScopedRateThrottle
from ninja_devx.contrib.redis_throttle import RedisThrottleStorage

redis_throttle_storage = RedisThrottleStorage(url="redis://cache:6379/1")

NINJA_DEVX = {"THROTTLE_STORAGE": "myapp.throttling.redis_throttle_storage"}


class UploadController(Controller):
    @post("/", throttle=[ScopedRateThrottle("uploads", storage=redis_throttle_storage)])
    def upload(self, request, file: UploadedFile) -> Upload: ...
```

- The default, `CacheThrottleStorage`, counts with `cache.add` + `cache.incr`, which is
  atomic on Redis and Memcached but not everywhere (a crash between the two calls loses
  one hit).
- `ninja_devx.contrib.redis_throttle.RedisThrottleStorage` (`pip install
  ninja-devx[redis]`) counts with `INCR` and a conditional `EXPIRE` (`NX`, so only the
  window's first hit sets the TTL) in one `MULTI`/`EXEC` transaction: an exact fixed
  window and a `Retry-After` that cannot drift from a race between the two calls.
- Pick a storage per throttle with `storage=`, or project-wide with
  `NINJA_DEVX["THROTTLE_STORAGE"]` (an instance, or its import path); an explicit `storage=`
  always wins.
- Write your own by implementing `hit(key, window_seconds) -> (count, retry_after)`.

## Request log

`RequestLogPlugin` logs one structured record per request and never the body:

```python
from ninja_devx.plugins import install
from ninja_devx.http.requestlog import RequestLogPlugin

install(api, [RequestLogPlugin(naming="otel")])
```

- Fields: `request_id`, `method`, `path`, `status`, `duration_ms`, `user_id`, `tenant`
  (the resolved tenant, or `request.tenant`), `query_count` (queries on the default
  database connection), `operation` (`"Controller.method"`), and `api_key_prefix` when
  `ninja_devx.contrib.apikeys` authenticated the request.
- `naming="otel"` (the default) spells `method`, `path`, `status` and `user_id` with an
  OpenTelemetry semantic convention name (`http.request.method`, `url.path`,
  `http.response.status_code`, `user.id`); `naming="flat"` keeps the plain names.
- The record goes through stdlib `logging` with the fields in `extra`; configure
  `ninja_devx.http.requestlog.JSONFormatter` in `LOGGING` to emit it as one JSON object.
- When `structlog` is installed (`pip install ninja-devx[structlog]`), the identity fields
  are also bound with `structlog.contextvars.bind_contextvars` as the request starts and
  cleared once the record is logged, so the application's own `structlog` calls carry
  them too.
- `RequestLogMiddleware` is the middleware alone, for `use_middleware()` or
  `ControllerOptions(middleware=[...])` without the bundled `RequestIDMiddleware`.

## Sensitive fields

`Sensitive` marks a schema field that must never be echoed back unmasked:

```python
from typing import Annotated
from ninja_devx.serialization.privacy import Sensitive


class UserOut(Schema):
    id: int
    email: str
    api_key: Annotated[str, Sensitive()]
```

- `mask(value)` returns `"***"` (`None` stays `None`; a list is masked element-wise).
- `redact_payload(schema, data)` returns a copy of `data` with every `Sensitive` field of
  `schema` masked, matched by field name or alias.
- The CSV/JSONL export (`ExportMixin`) masks `Sensitive` output fields unless the
  controller sets `export_sensitive = True`.
- `ninja_devx.http.errors.mask_validation_input(errors, schema)` masks the `input` a
  validation error body would otherwise echo back; Ninja's own request validation already
  drops it, so this matters for a `pydantic.ValidationError` you map and render yourself.
- `ninja_devx.contrib.audit.privacy.AuditPrivacy(schema=...)` treats a schema's `Sensitive`
  fields the same as its own `redact` names.
