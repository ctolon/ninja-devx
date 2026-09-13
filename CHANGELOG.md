# Changelog

All notable changes to this project are documented here. ninja-devx follows
[semantic versioning](https://semver.org); see the support and stability page for what
that means before 1.0.

## 0.0.1

First alpha release candidate. The section below is the reviewed release content;
publication occurs only when the matching tag passes the release workflow.

### Compatibility and operational requirements

- Python 3.11+, Django 4.2+ and django-ninja 1.7.x (`<2`); supported interpreter/framework
  combinations are listed in the support policy.
- Core migrations are required for durable idempotency and upload lifecycle records.
  Contrib apps have their own migrations and optional dependencies.
- Owner/tenant/parent scope and persisted object permissions apply to built-in writes.
  Audit history is paginated and staff-protected by default.
- Idempotency requires independent durable claims; webhook delivery is at-least-once;
  upload checksum/version enforcement is configurable.
- Generated clients support JSON, simple multipart and declared text/binary responses.
  Cookie parameters, SSE and complex encodings fail generation explicitly.
- Internal modules were reorganized before release. See the
  [migration notes](https://ctolon.github.io/ninja-devx/migration/review-integration/).

### Foundations

- Class-based controllers compiled into native `ninja.Router`s: `@get/@post/...` with
  typed options, request and singleton scopes, lifecycle methods, static-first routing,
  `mount()` with versioned prefixes.
- Composable permissions (`&`, `|`, `~`, `Also`), object-level and async checks,
  `DjangoModelPermissions`, `IsOwner`, typed users (`AuthedRequest[User]`,
  `current_user`).
- Generic CRUD (`CRUDController[Model, Out, In]` and mixins): `owner_field`,
  `Instance[Model]`, generated filters and ordering, nested `Parent`, soft delete, bulk
  operations, `Patch[In]`, `idempotent()`, `atomic=True`.
- A small typed DI container plus dishka and svcs adapters.
- Operation hooks, `LoggingHook`, OpenTelemetry.
- pytest plugin with OpenAPI snapshots, `devx_scaffold` and `devx_openapi` (typed
  TypeScript and Python clients).
- mypy strict and pyright strict, with no `typing.Any` in the package.

### APIs and integrations

**Errors and layers**

- `ErrorMap`: typed exception → response rules on operations, controllers, settings
  (`NINJA_DEVX["ERRORS"]`) and APIs (`install(api)`, using Ninja's exception handlers).
- Django `ValidationError`, `ObjectDoesNotExist` and `PermissionDenied` map to 422, 404
  and 403 by default.
- `raises=` documents errors in OpenAPI and is checked against the rules.
- `ERROR_FORMAT = "problem+json"` (RFC 9457).
- `ninja_devx.layers`, HTTP-free: `DomainError` (`NotFound`, `Conflict`,
  `PermissionDenied`, `ValidationFailed`), `Repository`/`AsyncRepository` protocols,
  `ModelRepository`, `ModelService`, `dual` (one method for sync and `.a` async callers),
  `RequestContext`, `TaskQueue` (`OnCommitTaskQueue`, `ImmediateTaskQueue`,
  `RecordingTaskQueue`), `after_commit`, `Policy`/`require`, `Selector`.
- `layers.testing`: `InMemoryRepository`, `make_context`.
- CRUD `service_class` and `selector_class`, resolved from the container.
- `use_case(decorator, Handler, command=...)` operations.
- Architecture recipes (HackSoft, Cosmic-lite, dishka interactors) with shared contract
  tests.

**Dependency injection**

- Operation-level injection: `Inject[T]` and `Annotated[T, Resolve(fn)]`, validated at
  startup.
- Container: async and async-generator factories, `aresolve`, `container.scope(values)`
  for tasks and commands, `close`/`aclose`, captive dependency checks, and
  `check(key, asynchronous=...)`.
- `request_context(User, tenant=...)` / `arequest_context`, `aauthenticated_user`,
  `acurrent_user`, `arequest_user`.
- dishka: `DishkaResolver(container, async_container=...)` for both modes,
  `provide_controllers`, startup validation.

**Async and sync**

- `mode = "sync" | "async" | "auto"` (`NINJA_DEVX["ASYNC_MODE"]`) and `async_variant` for
  reusable bases. The CRUD mixins implement both modes from one class.
- `run_sync`/`run_atomic`: one thread hop per unit of work. Async CRUD writes take a
  single hop.
- `AsyncOperationHook` (`around_async`), blocking sync hooks (`blocking = True`), async
  lifecycle methods, async permission checks everywhere.
- Hooks and DI scopes wrap whole streams; permissions are checked before the first item
  of async streams.
- Teaching errors: `AsyncLazyAccessError`, `BlockingCallWarning` (`WARN_BLOCKING_MS`),
  `MixedPathWarning`, `ASYNC_FETCH_MODE = "raise"` (Django 6.1 `FETCH_RAISE`), and the
  pytest `AsyncDatabaseTestWarning`.
- `python -m ninja_devx.tooling.unasync` generates sync twins of async code (`--check` for CI).

**Permissions and operations**

- `Also(...)` adds operation permissions to the controller's.
- `IsReadOnly`, `as_permission(policy, User)`.
- `owner_field` follows relations (`"project__owner"`).
- `Locked[Model]` (`select_for_update`).
- Typed operation metadata: `meta=(...)` and `operation.meta(Kind)`.
- `atomic="durable"` and `database=`.
- `ControllerPlugin` protocol (`on_operation`, `bindings`, `checks`), via options or
  `NINJA_DEVX["PLUGINS"]`.

**Quality and tooling**

- Django system checks (`ninja_devx.E001`–`E004`, `W003`), `Controller.checks()`,
  `NINJA_DEVX["CHECK_APIS"]`.
- `manage.py devx_scaffold --check` reports model/schema drift.
- Query optimizer v2: `Prefetch` querysets join foreign keys inside prefetched relations;
  `optimize_queries = "only"`; `query_plan()`.
- Testing: `assert_max_hops`, `capture_commits` / `captured_commits` fixture.
- Benchmarks v2: interleaved in-process overhead with thread-hop counts and an optional
  budget check (`benchmarks/budget.json`), plus WSGI/ASGI load tests with p50/p99
  (`benchmarks/load.py`).
- Docs: errors, layers, async and sync, system checks, recipes, support policy; every
  code block in the docs is compiled by the test suite.

### SaaS, HTTP and schemas

- Multi-tenancy: `tenant_field`, tenant resolvers (sync or async, settings, `RequestContext`,
  `request.tenant`), `Parent(..., tenant_field=...)`, `current_tenant(request)`.
- Conditional requests: `etag = ETag()` (304 on reads, `If-Match` → 412/428 on writes),
  `conditional()` for any GET.
- `CursorPagination` following the request's ordering; `pagination_options`.
- Throttles: `UserRateThrottle`, `AnonRateThrottle`, `ClientRateThrottle`,
  `ScopedRateThrottle` (`THROTTLE_RATES`), `TenantRateThrottle`; thread-safe and atomic.
- Role-based field visibility: `VisibleTo(...)` and the `FieldVisibility` mixin.
- Contract tests: `ninja_contract` fixture on schemathesis (`[contract]` extra).
- Nothing hard-coded: `routes` (rename, reconfigure or disable operations), `lookup_param`,
  `ordering_param`, `SoftDelete(field, deleted=, active=, deleted_at=, deleted_by=)`.
- Invalid lookup values reach Ninja and return JSON 422 (path converters are opt-in with
  `lookup_converter`).
- Schema generation: `devx_scaffold` writes model constraints (`max_length`, `min_length`,
  patterns, ranges, decimals, `help_text`); `devx_openapi --format python` generates
  validated pydantic models (default) or TypedDicts; drift checks flag `max_length`
  mismatches; system check `W005`.

### Security and optional integrations

- Object permissions: `ObjectPermissions()` on any model controller (DRF
  `DjangoObjectPermissions` semantics: 404 without `view`, 403 without the method's
  permission, lists filtered in SQL), backends for the new `ninja_devx.contrib.grants`
  app, django-guardian (`[guardian]`) and `user.has_perm`; `assign_perm`, `remove_perm`,
  `get_perms`, `get_objects_for_user`, `grants_for`; `ObjectSharingMixin` endpoints;
  `OBJECT_PERMISSION_BACKEND` setting.
- Response shaping: `sparse_fields` (`?fields=`) and `Expandable()` relations
  (`?expand=`), with the query planner joining only what is expanded.
- `AutoCRUDController[Model]` and `AutoReadOnlyController[Model]`: schemas generated with
  Ninja's `create_schema` (`schema_fields`, `read_only_fields`, `write_only_fields`).
- `LimitOffsetPagination`: links that keep query parameters, `count=False`, `max_offset`,
  stable ordering.
- `ExportMixin` / `ImportMixin`: streamed CSV and JSON Lines export honoring filters and
  visibility; all-or-nothing imports with row-level 422 errors and `dry_run`.
- Middleware for APIs, routers, mounts and controllers (`use_middleware`,
  `ControllerOptions(middleware=...)`) on Ninja's `add_decorator`: `RequestIDMiddleware`,
  `ServerTimingMiddleware`, `DeprecationMiddleware` (RFC 9745 / RFC 8594),
  `RateLimitHeadersMiddleware` (added automatically with ninja-devx throttles).
- `HealthController` (`/live`, `/ready`) with database, cache and migration checks.
- `ORJSONRenderer` and `MsgspecRenderer`; `OpenTelemetryMetricsMiddleware`.
- `contrib.apikeys`: hashed, scoped, expiring API keys (`APIKeyAuth`, `APIKeyBearer`,
  `HasScopes`, `RequiresScope`), a self-service controller and `devx_apikey`.
- `contrib.audit`: `AuditMixin` records creates, updates (field diffs) and deletes in the
  same transaction, with redaction and request ids; `record()`, `AuditLogController`,
  `AuditHistoryMixin`.
- `contrib.webhooks`: transactional outbox (`publish`), Standard Webhooks signatures,
  retries with backoff, endpoint disabling, `SKIP LOCKED` workers (`devx_webhooks
  deliver`), `WebhookEndpointController`, `verify_signature` for receivers.
- `contrib.uploads`: presigned direct uploads (`S3Signer` with `[s3]`, `FakeSigner`),
  upload policies and ownership checks.
- Webhooks hardening: SSRF protection (`URLPolicy`, `check_url`, `SafeHTTPTransport` checks
  the connected address, so DNS rebinding is covered too), optional encryption of signing
  secrets (`WEBHOOK_SECRET_KEYS`, `[crypto]`, `devx_webhooks generate-key` /
  `encrypt-secrets`), and delivery right after commit through task queues
  (`publish(queue=...)`, `deliver_event`, `django.tasks` task).
- API keys: per-key `rate_limit` and `APIKeyRateThrottle`.
- `sparse_fields` operations document `<Out>Partial` responses (no required properties), so
  validating generated clients accept `?fields=` responses.
- Django admin for `ObjectGrant`, `APIKey` (revoke action), `AuditEntry` (read-only) and
  webhooks (secret shown once, rotate, retry, enable).
- Translations: client-facing messages use `gettext` (`default_message` on domain errors,
  `gettext_noop` permission messages), and `tools/messages.py` to extract and compile
  catalogs without GNU gettext. English defaults ship; add catalogs for other languages.
- `ObjectSharingMixin.validate_holder()` restricts who objects may be shared with.
- `devx_startapp`: Django's `startapp` with a ninja-devx template.
- "Did you mean" suggestions for misspelled fields, routes and settings.
- Project: `SECURITY.md`, `CONTRIBUTING.md`, versioned documentation with mike, a
  PostgreSQL test job and SQLite/PostgreSQL load tests in CI.

### Known limitations

- A permission denial on an async streaming operation closes the stream empty (with a
  logged warning) instead of returning 403: Ninja starts the response before the stream
  runs.
- `Container.instance` and `Container.override` validate values at registration rather
  than in type checkers, because mypy cannot infer the type from abstract or protocol
  keys.
