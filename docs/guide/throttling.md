# Throttling

!!! tip "Reference"

    Every option on this page, with types and defaults: [configuration reference](../options/http.md#throttles).

Throttles plug into Ninja's own `throttle=` option (operation, controller or API), so
rejected requests get Ninja's 429 with `Retry-After`.

```python
from ninja_devx.http.throttling import ClientRateThrottle, ScopedRateThrottle, UserRateThrottle

NINJA_DEVX = {"THROTTLE_RATES": {"uploads": "10/min", "exports": "5/hour"}}


class FileController(Controller):
    options = ControllerOptions(throttle=[ClientRateThrottle(user="600/min", anon="30/min")])

    @post("/", throttle=[ScopedRateThrottle("uploads")])
    def upload(self, request: HttpRequest, file: UploadedFile) -> FileOut: ...
```

| Throttle | Counts per | Notes |
|---|---|---|
| `UserRateThrottle("1000/day")` | authenticated user | anonymous requests pass |
| `AnonRateThrottle("60/min")` | client IP | authenticated requests pass |
| `ClientRateThrottle(user=..., anon=...)` | user, or IP when anonymous | two rates in one throttle |
| `ScopedRateThrottle("uploads")` | user or IP, per scope | rate from `NINJA_DEVX["THROTTLE_RATES"]`; operations with the same scope share a budget |
| `TenantRateThrottle("10000/day")` | tenant | the resolved tenant, `request.tenant` or `tenant=` / `TENANT_RESOLVER` |

Rates look like `"100/min"`, `"1000/day"` or `"20/5min"`. Several throttles on one
operation must all pass: `throttle=[burst, sustained]`.

## Rate limit headers

Controllers using these throttles also send the current budget, on successful responses
as well as on 429s. `RateLimitHeadersMiddleware` is added automatically:

```http
RateLimit-Limit: 600
RateLimit-Remaining: 597
RateLimit-Reset: 42
RateLimit-Policy: "user";q=600;w=60, "anon";q=30;w=60
```

The most restrictive throttle of the request is reported. For plain Ninja routers, add
the headers with `use_middleware(router, RateLimitHeadersMiddleware())`. See
[Middleware](middleware.md).

## How it differs from Ninja's built-ins

- **Thread-safe.** Nothing request-specific is stored on the shared throttle object.
- **Atomic across processes.** Counting uses fixed windows with `cache.add` +
  `cache.incr`, which is atomic on Redis and Memcached. Use a shared cache in production
  (`cache="throttle"` picks the alias).
- **Settings read per request.** `override_settings` works in tests.
- **Custom identities.** Subclass `RateThrottle` and implement
  `identify(request) -> str | None`; return `None` to skip throttling.
