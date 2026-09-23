# Changelog

All notable changes to this project are documented here. ninja-devx follows
[semantic versioning](https://semver.org); see the support and stability page for what
that means before 1.0.

## 0.0.2

Second alpha. Focuses on module boundaries, introspection, explicit query planning and a
wider client generator. APIs may still change before 1.0.

### Added

- `devx_inspect` command: prints the resolved policy for a mounted controller (tenant,
  owner, parent, permissions, transaction, idempotency, pagination, relations and hooks)
  as a tree or JSON.
- `@requires_related(...)` and the `related` controller attribute: explicit
  `select_related`/`prefetch_related` hints the N+1 planner applies when a schema cannot
  be analysed statically. System check `ninja_devx.W006` validates the hints.
- `ninja_devx.cqrs`: `use_query` (read handlers, symmetric to `use_case`), optional
  `Command`/`Query` markers, `DomainEvent` + `EventBus` delivered after commit through a
  `TaskQueue`, and `UnitOfWork` for multi-repository transactions. No command bus or
  global registry.
- HTTP middleware: `SecurityHeadersMiddleware` (HSTS/CSP/referrer/frame options),
  request hardening (`MaxBodySizeMiddleware`, `EnforceContentTypeMiddleware`,
  `JsonDepthMiddleware`), `ResponseCacheMiddleware` with prefix invalidation (`private`
  when varying on credentials), and `PaginationHeadersMiddleware` (RFC 8288 `Link` for
  offset and cursor pagination, `X-Total-Count` for counted offset pages). Rejections use
  the package error format.
- `devx_openapi --against baseline.json`: fail on breaking OpenAPI changes (removed
  operations/fields, new required input, type/enum/array item changes); additive changes
  are reported. With `--output` the current document is written after the comparison.
- Pluggable `SearchBackend` for list search (`IContainsSearch`, `PostgresSearch`); the
  `search` parameter stays in the filter schema and OpenAPI.
- Write-side field visibility: `WriteVisibleTo(permission)` on input schemas; model
  controllers reject forbidden fields on create/update with 403.
- Soft-delete helpers: `soft_delete_cascade` marks related objects with the parent, and
  `soft_delete_unique(Model, "field")` builds a partial unique constraint for active rows.
- Abstract model bases in `ninja_devx.models`: `TimeStamped` (`created_at`/`updated_at`),
  `UserStamped` (`created_by`/`updated_by`, filled from the request user on create, update,
  bulk update and import), `Stamped` (both) and `SoftDeletable` (`deleted_at`/`deleted_by`,
  used by `SoftDeleteMixin` without configuration). The columns are `editable=False`, so
  generated input schemas and scaffolding leave them out.
- Task queue adapters in `ninja_devx.contrib.tasks`: Celery, Dramatiq, RQ, Taskiq, Temporal
  and FastStream behind the existing `TaskQueue` protocol, plus a generic
  `DeferredTaskQueue`.
- `ninja_devx.testing`: `sample`/`samples` build valid, JSON-serialisable request payloads
  from a schema and raise `TypeError` for a field type they cannot fill.
- `devx_apikey rotate` and `rotate_api_key` replace a key's secret in place; revoked keys
  are refused and `rate_limit=None` clears the limit.
- `APIPlugin`/`install`: bundle API middleware, error rules and startup work; error rules
  from all plugins are merged into one `ErrorMap`.
- Client generation: OpenAPI `discriminator` (tagged unions) for Python and TypeScript,
  multipart bodies without a required file, cookie parameters, and `text/event-stream`
  responses as a Python line iterator (`Iterator[str]`/`AsyncIterator[str]`). Documented
  non-2xx JSON responses are exposed on the parsed operation (`Operation.errors`). The
  TypeScript client rejects streaming. Clients without cookie or streaming operations
  regenerate unchanged.
- `benchmarks/frameworks/`: a cross-framework workload characterization over one HTTP
  contract (Django Ninja, ninja-devx and optional DRF/Ninja-Extra adapters); a local tool,
  not a CI gate.
- Query tuning: `expand_rules` lets a controller filter, order and cap how many rows
  `?expand=` loads per parent (window function on Django 5+, correlated subquery on 4.2);
  `QueryExplainMiddleware` adds `X-Query-Count`/`X-Query-Time`/`X-Query-Plan` headers in
  development; and `LimitOffsetPagination(count=...)` also accepts `"estimate"`
  (PostgreSQL `reltuples`) and an integer threshold (`X-Total-Count: N+`).
- Nested writes (`NestedWritesMixin`, `Nested`) for creating/updating a parent and its
  child collections in one request and transaction, and change tracking
  (`ModelController.on_change`, `changed_fields()`) for PUT/PATCH and bulk updates.
- `TransitionsMixin`/`Transition` for API-level state transitions on model controllers
  (`POST /{pk}/<name>`, `GET /{pk}/transitions`) with permissions, guards, row locking and
  a 409 `invalid_transition` error.
- `bulk_partial` on `BulkCreateMixin`/`BulkUpdateMixin`: per-item savepoints and a 207
  partial-success response (`results`, `X-Bulk-Failed` header).
- A metadata endpoint (`MetaMixin`), an aggregation endpoint (`AggregateMixin`), response
  versioning (`VersionedResponseMixin`), and OpenAPI examples generated from sample data
  (`with_examples`/`openapi_examples`).
- Pluggable throttle storage (with a Redis-backed exact fixed window), a
  `RequestLogPlugin` for structured per-request logs, and a `Sensitive` field marker
  applied to CRUD exports, validation-error bodies and the audit log.
- `ninja_devx.contrib.jobs`: background jobs tracked as `Job` rows, started with
  `start_job`/`@job`, polled through `JobsController`, with `devx_jobs prune`/`retry`
  maintenance (new app; add to `INSTALLED_APPS` and migrate).
- Runtime N+1 detection (`ninja_devx.contrib.nplusone`, an adapter over django-zeal) with
  a `strict_queries` test fixture, and the `devx_doctor` management command for
  configuration-risk findings beyond `manage.py check`.
- `manage.py devx_startproject`: generates a runnable ninja-devx project (settings,
  hardened API, health check, JSON logging, DATABASE_URL-based database, Postgres compose
  file) with optional `--app` and `--no-docker`.

### Changed

- `GrantsBackend` moved from `ninja_devx.security.object_permissions` to
  `ninja_devx.contrib.grants.backends`; update imports. The built-in object-permission
  registry resolves backends by name.
- Core packages no longer import `ninja_devx.crud` or `ninja_devx.contrib`; a test
  enforces the boundary.
- The Docker validation enforces a 90% statement/branch coverage floor
  (`tools/verify_local.py --fail-under`).
- Documentation: new **CQRS and DDD** and **Inspecting controllers** guides, expanded
  typing and mounting pages, a guide link on every configuration-reference page, and a
  dedicated Migrations section in the navigation.
- `LimitOffsetPagination(count=...)` widens from `bool` to `bool | Literal["estimate"] |
  int`; existing `True`/`False` usage is unaffected. `RateThrottle(storage=...)` is
  additive; throttles default to `CacheThrottleStorage` as before.

### Fixed

- Release documentation reflects that 0.0.1 is published.
- `tools/messages.py` accepts `--language` so it is usable without a shipped catalog.

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
