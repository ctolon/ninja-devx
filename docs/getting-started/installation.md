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
