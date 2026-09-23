# Configuration reference

Every class attribute, option, parameter, setting, command flag, error code and system
check, with its type, default and meaning.

!!! info "Generated from the code"

    These pages are produced by `tools/generate_docs.py` from the source: types and
    defaults exactly as written, descriptions from docstrings. A test fails when they are
    out of date, so they always match the installed version.

| Page | Covers | Guide |
|---|---|---|
| [Controllers and operations](controllers.md) | `Controller` attributes, `as_router()`, `ControllerOptions`, `OperationOptions`, `RouteOptions`, `mount()`, `use_case()`, hooks | [Controllers](../guide/controllers.md), [Mounting](../guide/mounting.md) |
| [CRUD](crud.md) | `ModelController` and list attributes, generated schemas, bulk, `ExpandRule`, nested writes, transitions, `MetaMixin`, `AggregateMixin`, import/export, sharing, `SoftDelete`, `Parent`, `ETag`, offset and cursor pagination, parameter annotations | [CRUD](../guide/crud.md), [Nested writes](../guide/nested-writes.md), [Transitions](../guide/transitions.md), [Query tuning](../guide/query-tuning.md), [API surface](../guide/api-surface.md) |
| [Permissions and visibility](permissions.md) | built-in permissions and their arguments, `as_permission()`, `ObjectPermissions`, backends and shortcuts, `VisibleTo`, `Expandable`, `Sensitive` | [Permissions](../guide/permissions.md), [Object permissions](../guide/object-permissions.md), [Visibility](../guide/visibility.md), [Operations](../guide/operations.md) |
| [Middleware, health and rendering](middleware.md) | middleware, `QueryExplainMiddleware`, `VersionedResponseMixin`, `RequestLogMiddleware`/`RequestLogPlugin`, `HealthController` and checks, renderers, OpenTelemetry | [Middleware and operations](../guide/middleware.md), [Operations](../guide/operations.md), [Query tuning](../guide/query-tuning.md) |
| [API keys, audit log, webhooks, uploads, jobs](contrib.md) | the `ninja_devx.contrib` apps, `redis_throttle`, `nplusone` | [API keys](../guide/api-keys.md), [Audit](../guide/audit.md), [Webhooks](../guide/webhooks.md), [Uploads](../guide/uploads.md), [Jobs](../guide/jobs.md), [Operations](../guide/operations.md), [N+1 detection and devx_doctor](../guide/doctor.md) |
| [Dependency injection](dependency-injection.md) | `Container` methods, `Inject`, `Resolve`, `request_context()`, dishka, svcs | [Dependency injection](../guide/dependency-injection.md) |
| [Tenancy, caching, throttling](http.md) | `current_tenant()`, `conditional()`, throttles, throttle storage, `idempotent()` | [Multi-tenancy](../guide/tenancy.md), [HTTP caching](../guide/conditional.md), [Throttling](../guide/throttling.md), [Operations](../guide/operations.md) |
| [Errors](errors.md) | `ErrorMap.map()`, `mask_validation_input()`, `DomainError`, built-in error codes, exceptions and warnings | [Errors](../guide/errors.md) |
| [Settings](settings.md) | every `NINJA_DEVX` key | [Settings](../guide/settings.md) |
| [Testing](testing.md) | pytest fixtures and options, test helpers, contract testing | [Testing](../guide/testing.md) |
| [Commands and checks](commands.md) | `devx_scaffold`, `devx_openapi`, `devx_inspect`, `devx_doctor`, `devx_startapp`, `devx_startproject`, `devx_apikey`, `devx_webhooks`, `devx_jobs`, `unasync`, system check ids | [Code generation](../guide/codegen.md), [Starting a project](../guide/startproject.md), [System checks](../guide/checks.md), [Inspecting](../guide/inspect.md), [N+1 detection and devx_doctor](../guide/doctor.md) |

The [API reference](../reference.md) renders every public module's docstrings.
