# Installation

```bash
pip install ninja-devx
```

| Extra | Adds | For |
|---|---|---|
| `ninja-devx[dishka]` | `dishka` | `ninja_devx.contrib.dishka.DishkaResolver` |
| `ninja-devx[svcs]` | `svcs` | `ninja_devx.contrib.svcs.SvcsResolver` |
| `ninja-devx[otel]` | `opentelemetry-api` | `ninja_devx.contrib.otel.OpenTelemetryHook` |
| `ninja-devx[client]` | `httpx` | generated Python clients |
| `ninja-devx[contract]` | `schemathesis` | the `ninja_contract` pytest fixture |
| `ninja-devx[guardian]` | `django-guardian` | the guardian object-permission backend |
| `ninja-devx[s3]` | `boto3` | presigned S3 uploads |
| `ninja-devx[crypto]` | `cryptography` | encrypted webhook signing secrets |
| `ninja-devx[orjson]` | `orjson` | the fast ORJSON renderer |
| `ninja-devx[msgspec]` | `msgspec` | the msgspec renderer |
| `ninja-devx[redis]` | `redis` | `ninja_devx.contrib.redis_throttle.RedisThrottleStorage` |
| `ninja-devx[structlog]` | `structlog` | binding request identity to `structlog.contextvars` in `RequestLogMiddleware` |
| `ninja-devx[zeal]` | `django-zeal` | runtime N+1 detection (`ninja_devx.contrib.nplusone`) |
| `ninja-devx[filters]` | `django-filter` | `filterset_class` on list endpoints |
| `ninja-devx[rules]` | `rules` | `ninja_devx.contrib.rules.HasRule` |

Requirements: Python 3.11+, Django 4.2+ and django-ninja 1.7+. See
[Support and stability](../project/support.md) for the tested matrix.

## Configure Django

```python
INSTALLED_APPS = [
    # ...
    "ninja_devx",  # management commands and system checks
]

NINJA_DEVX = {  # optional: every key has a default
    "PAGINATION_CLASS": "ninja.pagination.PageNumberPagination",
    "CHECK_APIS": ["config.urls.api"],
}
```

All keys are listed in the [settings reference](../options/settings.md). The pytest plugin
loads automatically; no configuration is needed.

## Editor and type checker

The package ships `py.typed`. For model field types in your own code, enable the
django-stubs mypy plugin:

```ini
[mypy]
strict = true
plugins = mypy_django_plugin.main

[mypy.plugins.django-stubs]
django_settings_module = config.settings
```

## Next

- [Quickstart](quickstart.md): a working API in five minutes.
- [Tutorial](tutorial.md): the blog example, step by step.
- [Guides](../guide/index.md): find the page for a task.
- [Inspecting controllers](../guide/inspect.md): see what a configuration resolves to.
