# Middleware and operations

!!! tip "Reference"

    Every option on this page, with types and defaults:
    [configuration reference](../options/middleware.md).

## Router and API middleware

Django middleware runs for every URL. Ninja-devx middleware runs only around the
operations of an API, a router, a mounted version or one controller. It is built on
Ninja's `add_decorator(..., mode="view")`, so it wraps authentication, throttling,
validation and the view, and it sees 401/422/429 responses too.

```python
from ninja_devx import ControllerOptions, mount
from ninja_devx.http.middleware import (
    DeprecationMiddleware,
    RequestIDMiddleware,
    ServerTimingMiddleware,
    use_middleware,
)

use_middleware(api, RequestIDMiddleware(), ServerTimingMiddleware())  # every operation
mount(
    api,
    V1,
    prefix="/v1",
    middleware=[DeprecationMiddleware(sunset=datetime(2027, 1, 1, tzinfo=UTC))],
)


class ReportController(Controller):
    options = ControllerOptions(middleware=[ServerTimingMiddleware()])
```

`use_middleware` works on plain Ninja routers with function views as well. Call it before
the router is added to the API. The first middleware is the outermost.

### Writing middleware

```python
class TenantHeader(Middleware):
    def process_request(self, request: HttpRequest) -> HttpResponseBase | None:
        if "X-Tenant" not in request.headers:
            return JsonResponse({"detail": "X-Tenant is required"}, status=400)  # short-circuit
        return None

    def process_response(
        self, request: HttpRequest, response: HttpResponseBase
    ) -> HttpResponseBase:
        response["X-Tenant"] = request.headers["X-Tenant"]
        return response
```

Both methods are optional. Async operations call `aprocess_request` and
`aprocess_response`, which default to the sync methods. Override them when the work does
I/O.

### Built-in middleware

| Middleware | Adds |
|---|---|
| `RequestIDMiddleware(header="X-Request-ID")` | accepts or generates a request id, echoes it; `get_request_id(request)` reads it (the audit log stores it) |
| `ServerTimingMiddleware()` | `Server-Timing: app;dur=12.3` |
| `DeprecationMiddleware(deprecated_at=, sunset=, link=)` | `Deprecation` (RFC 9745), `Sunset` (RFC 8594), `Link: <...>; rel="deprecation"` |
| `RateLimitHeadersMiddleware()` | `RateLimit-Limit`, `RateLimit-Remaining`, `RateLimit-Reset`, `RateLimit-Policy`, added automatically with ninja-devx throttles |
| `OpenTelemetryMetricsMiddleware(meter=None)` | `http.server.request.duration` and `http.server.active_requests` metrics |

## Health checks

```python
from ninja_devx.http.health import CacheCheck, DatabaseCheck, HealthController, MigrationsCheck


class Health(HealthController):
    health_checks = (DatabaseCheck(), CacheCheck(), MigrationsCheck())


mount(api, {"/health": Health})
```

- `GET /health/live` answers 200 while the process runs; use it as a liveness probe.
- `GET /health/ready` runs every check. It answers 200, or 503 with the failing checks:

```json
{"status": "error", "checks": [{"name": "database", "status": "error", "duration_ms": 3.1, "error": "dependency_unavailable"}]}
```

Readiness has a total `health_timeout` deadline (default 2 seconds). Checks run concurrently
in at most one worker per configured check. A timed-out check keeps its slot until it
returns, so repeated probes cannot create an unbounded queue. Timeouts produce
`dependency_timeout`; other failures produce `dependency_unavailable`. Detailed exceptions
are logged server-side. Configure driver/network timeouts as well: Python cannot forcibly
stop a stuck check thread. Workers close their own database and cache connections.

`HealthController` uses `Scope.SINGLETON` so the runner belongs to one mounted router;
keep that scope when mounting or subclassing. Checks must be thread-safe and use
thread-local dependency connections. No check runs on `/live`.

Neither endpoint requires authentication. A check is any object with a `name` and a
`check()` method that raises on failure:

```python
@dataclass(frozen=True)
class QueueCheck:
    name: str = "queue"

    def check(self) -> None:
        redis.ping()
```

## Faster JSON

```python
from ninja_devx.serialization.renderers import ORJSONRenderer

api = NinjaAPI(renderer=ORJSONRenderer())  # pip install "ninja-devx[orjson]"
```

`ORJSONRenderer` and `MsgspecRenderer` are Ninja renderers. They give the same output as
Ninja's encoder (UTC datetimes with `Z`, decimals as strings, pydantic models), with one
difference: datetimes keep their microseconds. Rendering large responses is faster;
measure with your own payloads using `benchmarks/overhead.py`.

## OpenTelemetry

Traces come from `OpenTelemetryHook` (one span per operation, see
[Hooks](hooks.md)), and metrics from `OpenTelemetryMetricsMiddleware`:

```python
from ninja_devx import ControllerOptions
from ninja_devx.contrib.otel import OpenTelemetryHook, OpenTelemetryMetricsMiddleware

NINJA_DEVX = {"DEFAULT_OPTIONS": ControllerOptions(hooks=[OpenTelemetryHook()])}
use_middleware(api, OpenTelemetryMetricsMiddleware())
```

Metrics unwind active request counters on exceptions and cancellation as well as regular
responses. Route labels use a resolver template, the compiled operation path, or the fixed
`<unmatched>` label; raw URLs never create one series per object ID. Nonstandard methods
use `_OTHER`. Reusing the middleware at nested API/router levels remains balanced.

Custom middleware can implement `process_exception(request, exception)` and its async
counterpart to release resources. Cleanup runs in reverse order for every entered hook
that did not finish its response callback, including a hook whose request callback failed.
It cannot replace the original exception; cleanup failures are logged and outer cleanup
continues. Normal error responses still use `process_response`.
