# Configuration reference

Every class attribute, option, parameter, setting, command flag, error code and system
check, with its type, default and meaning.

!!! info "Generated from the code"

    These pages are produced by `tools/generate_docs.py` from the source: types and
    defaults exactly as written, descriptions from docstrings. A test fails when they are
    out of date, so they always match the installed version.

| Page | Covers |
|---|---|
| [Controllers and operations](controllers.md) | `Controller` attributes, `as_router()`, `ControllerOptions`, `OperationOptions`, `RouteOptions`, `mount()`, `use_case()`, hooks |
| [CRUD](crud.md) | `ModelController` and list attributes, generated schemas, bulk, import/export, sharing, `SoftDelete`, `Parent`, `ETag`, offset and cursor pagination, parameter annotations |
| [Permissions and visibility](permissions.md) | built-in permissions and their arguments, `as_permission()`, `ObjectPermissions`, backends and shortcuts, `VisibleTo`, `Expandable` |
| [Middleware, health and rendering](middleware.md) | middleware, `HealthController` and checks, renderers, OpenTelemetry |
| [API keys, audit log, webhooks, uploads](contrib.md) | the `ninja_devx.contrib` apps |
| [Dependency injection](dependency-injection.md) | `Container` methods, `Inject`, `Resolve`, `request_context()`, dishka |
| [Tenancy, caching, throttling](http.md) | `current_tenant()`, `conditional()`, throttles, `idempotent()` |
| [Errors](errors.md) | `ErrorMap.map()`, `DomainError`, built-in error codes, exceptions and warnings |
| [Settings](settings.md) | every `NINJA_DEVX` key |
| [Testing](testing.md) | pytest fixtures and options, test helpers, contract testing |
| [Commands and checks](commands.md) | `devx_scaffold`, `devx_openapi`, `devx_startapp`, `devx_apikey`, `devx_webhooks`, `unasync`, system check ids |

The [API reference](../reference.md) renders every public module's docstrings.
