# Settings

!!! tip "Reference"

    Every option on this page, with types and defaults: [configuration reference](../options/settings.md).

```python
NINJA_DEVX = {
    # Controllers
    "DEFAULT_OPTIONS": ControllerOptions(auth=JWTAuth()),
    "DOCUMENT_ERRORS": True,
    "PLUGINS": ["project.api.TenantHeaderPlugin"],
    # CRUD
    "PAGINATION_CLASS": "ninja.pagination.PageNumberPagination",
    "VALIDATE_MODEL": True,
    "REFRESH_AFTER_WRITE": True,
    "OPTIMIZE_QUERIES": True,  # or "only"
    "BULK_LIMIT": 100,
    # Errors
    "ERRORS": "project.errors.error_map",
    "ERROR_FORMAT": "ninja",  # or "problem+json"
    # Async
    "ASYNC_MODE": "sync",  # what mode = "auto" controllers use
    "WARN_BLOCKING_MS": None,  # e.g. 20 in development
    "ASYNC_FETCH_MODE": "lazy",  # "raise": lazy loads fail in async operations (Django 6.1+)
    # Idempotency
    "IDEMPOTENCY_TTL": 86400,
    "IDEMPOTENCY_DATABASE": "default",
    # Multi-tenancy and throttling
    "TENANT_RESOLVER": "project.tenancy.org_from_header",
    "TENANT_CONTEXT": None,  # or a RequestContext[User, Org] key (or its import path)
    "THROTTLE_RATES": {"uploads": "10/min"},
    # Checks
    "CHECK_APIS": ["config.urls.api"],
}
```

| Key | Default | Docs |
|---|---|---|
| `DEFAULT_OPTIONS` | `{}` | options below every controller's ([Controllers](controllers.md#options)) |
| `DOCUMENT_ERRORS` | `True` | document 401/403/404/422 |
| `PLUGINS` | `()` | `ControllerPlugin` objects or import paths ([Controllers](controllers.md#plugins)) |
| `PAGINATION_CLASS` | `None` | CRUD list pagination |
| `VALIDATE_MODEL` | `True` | `full_clean()` on writes (422) |
| `REFRESH_AFTER_WRITE` | `True` | reload through `scoped_queryset` after writes |
| `OPTIMIZE_QUERIES` | `True` | [N+1 queries](crud.md#n1-queries) |
| `BULK_LIMIT` | `100` | objects per bulk request |
| `ERRORS` | `None` | an `ErrorMap` or its import path ([Errors](errors.md)) |
| `ERROR_FORMAT` | `"ninja"` | `"problem+json"` for RFC 9457 |
| `ASYNC_MODE` | `"sync"` | [Async and sync](async.md) |
| `WARN_BLOCKING_MS` | `None` | `BlockingCallWarning` threshold |
| `ASYNC_FETCH_MODE` | `"lazy"` | `"raise"` to forbid lazy loads in async operations |
| `IDEMPOTENCY_TTL` / `IDEMPOTENCY_DATABASE` | `86400` / `"default"` | `idempotent()` |
| `TENANT_RESOLVER` | `None` | function of the request returning the tenant ([Multi-tenancy](tenancy.md)) |
| `TENANT_CONTEXT` | `None` | `RequestContext` key whose `tenant` is used |
| `THROTTLE_RATES` | `{}` | rates of `ScopedRateThrottle` scopes ([Throttling](throttling.md)) |
| `CHECK_APIS` | `()` | APIs inspected by `manage.py check` ([Checks](checks.md)) |

Class attributes on your controllers override settings. Unknown keys raise
`ImproperlyConfigured`. Settings are re-read when `override_settings` changes them.
