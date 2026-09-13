# ninja-devx

ninja-devx adds typed controllers and reusable resource policies to
[Django Ninja](https://django-ninja.dev). It is intended for Django APIs that repeat
ownership, tenant scoping, transaction, dependency-lifetime and error-handling rules
across several endpoints. Services, repositories and contrib modules are optional.

The package is preparing its first **0.0.1 alpha** release. See the
[scope and design rationale](docs/project/scope.md), [comparison](docs/project/comparison.md)
and [support policy](docs/project/support.md) before adopting it.

```python
class PostController(SoftDeleteMixin[Post, PostOut], CRUDController[Post, PostOut, PostIn]):
    owner_field = "author"
    search_fields = ("title", "body")
    filter_fields = {"status": ("exact",), "created": ("gte", "lte")}
    ordering_fields = ("created", "title")

    @post("/{pk}/publish", response=PostOut, decorators=[idempotent()])
    def publish(self, request: HttpRequest, post: Instance[Post]) -> Post:
        post.status = Post.Status.PUBLISHED
        post.save(update_fields=["status"])
        return post


api = NinjaAPI(auth=django_auth)
mount(api, {"/posts": PostController}, prefix="/v1")
```

These lines give you:

- list endpoints with search, generated filters, enum-checked ordering and pagination
- retrieve, create, update and partial update
- soft delete, restore and an idempotent publish action
- owner-only writes and schema-derived relation loading
- 422 for model validation errors, and domain errors mapped without exception handlers
- declared error responses included in OpenAPI

**Principles:**

- Controllers produce `ninja.Router`s and use Ninja authentication, pagination and
  serialization. Async stream preflight has a narrow operation-subclass integration
  that is tested against supported Ninja versions.
- Layers without force: plain controllers, services, use cases or dishka interactors all
  fit, and none is required.
- Typed end to end: mypy strict and pyright strict pass, and the package has no `typing.Any`.
- Configuration errors raise at startup.
- Controller overhead is measured against function views with explicit budgets (see
  [performance](https://github.com/ctolon/ninja-devx/blob/main/docs/project/performance.md)).

Requires Python 3.11+, Django 4.2+ and django-ninja 1.7+ ([support policy](https://github.com/ctolon/ninja-devx/blob/main/docs/project/support.md)).

```bash
pip install ninja-devx==0.0.1    # after publication; extras: [guardian] [s3] [crypto] [orjson] [msgspec] [dishka] [svcs] [otel] [client] [contract]
```

## Features

| Area | What you get | Docs |
|---|---|---|
| Controllers | `@get/@post/...` with typed options, scopes, lifecycle methods, plugins, `use_case` | [guide](https://github.com/ctolon/ninja-devx/blob/main/docs/guide/controllers.md) |
| Permissions | composable `&` `\|` `~`, `Also(...)`, object-level, async, policies, `AuthedRequest[User]` | [guide](https://github.com/ctolon/ninja-devx/blob/main/docs/guide/permissions.md) |
| Object permissions | per-object grants (built-in table or django-guardian), DRF-style 404/403, lists filtered in SQL, sharing endpoints | [guide](https://github.com/ctolon/ninja-devx/blob/main/docs/guide/object-permissions.md) |
| Errors | `DomainError` and typed `ErrorMap` rules per operation, controller, project; `problem+json` | [guide](https://github.com/ctolon/ninja-devx/blob/main/docs/guide/errors.md) |
| CRUD | sync and async from one class, `AutoCRUDController[Model]`, `owner_field`, `Instance`/`Locked`, filters, offset and cursor pagination, nested, configurable soft delete and routes, bulk, CSV/JSONL import and export, N+1 planner | [guide](https://github.com/ctolon/ninja-devx/blob/main/docs/guide/crud.md) |
| SaaS and HTTP | `tenant_field` multi-tenancy, `ETag`/304/`If-Match` optimistic locking, user/scope/tenant throttles, role-based field visibility, `?fields=`/`?expand=` | [tenancy](https://github.com/ctolon/ninja-devx/blob/main/docs/guide/tenancy.md), [caching](https://github.com/ctolon/ninja-devx/blob/main/docs/guide/conditional.md), [throttling](https://github.com/ctolon/ninja-devx/blob/main/docs/guide/throttling.md), [visibility](https://github.com/ctolon/ninja-devx/blob/main/docs/guide/visibility.md) |
| Layers | HTTP-free services, repositories, `RequestContext`, after-commit tasks, policies, selectors, in-memory fakes | [guide](https://github.com/ctolon/ninja-devx/blob/main/docs/guide/layers.md) |
| DI | `Inject[T]`, `Resolve(fn)`, a checked container with async factories, dishka and svcs adapters | [guide](https://github.com/ctolon/ninja-devx/blob/main/docs/guide/dependency-injection.md) |
| Async | `mode`, one thread hop per unit of work, async hooks, loud lazy-load errors, `unasync` | [guide](https://github.com/ctolon/ninja-devx/blob/main/docs/guide/async.md) |
| Cross-cutting | hooks, `LoggingHook`, OpenTelemetry, `atomic=True`, `idempotent()` | [guide](https://github.com/ctolon/ninja-devx/blob/main/docs/guide/hooks.md) |
| Operations | router/API middleware, request ids, deprecation and rate limit headers, health checks, orjson/msgspec renderers | [guide](https://github.com/ctolon/ninja-devx/blob/main/docs/guide/middleware.md) |
| Optional integrations | scoped, rate-limited API keys, audit log with diffs, transactional outbox and signed, destination-validated webhooks with encrypted secrets, presigned S3 uploads, Django admin for the credential, audit and webhook records | [API keys](https://github.com/ctolon/ninja-devx/blob/main/docs/guide/api-keys.md), [audit](https://github.com/ctolon/ninja-devx/blob/main/docs/guide/audit.md), [webhooks](https://github.com/ctolon/ninja-devx/blob/main/docs/guide/webhooks.md), [uploads](https://github.com/ctolon/ninja-devx/blob/main/docs/guide/uploads.md) |
| Quality | system checks, `devx_scaffold --check` schema drift, schemathesis contract tests, OpenAPI snapshots, `assert_max_queries`/`assert_max_hops` | [checks](https://github.com/ctolon/ninja-devx/blob/main/docs/guide/checks.md), [testing](https://github.com/ctolon/ninja-devx/blob/main/docs/guide/testing.md) |
| Code generation | `devx_startapp`, `devx_scaffold` with model constraints, TypeScript and validated pydantic Python clients | [guide](https://github.com/ctolon/ninja-devx/blob/main/docs/guide/codegen.md) |
| Settings and translations | `NINJA_DEVX` project defaults; client-facing messages use Django's `gettext`, so you can ship your own catalog | [guide](https://github.com/ctolon/ninja-devx/blob/main/docs/guide/i18n.md) |

Start with the [quickstart](https://github.com/ctolon/ninja-devx/blob/main/docs/getting-started/quickstart.md), then pick an
example:

| Example | Shows |
|---|---|
| [`quickstart`](https://github.com/ctolon/ninja-devx/tree/main/examples/quickstart) | a private notes API in one small file |
| [`blog`](https://github.com/ctolon/ninja-devx/tree/main/examples/blog) | the tutorial: scaffolding, soft delete, nesting, generated clients |
| [`recipes`](https://github.com/ctolon/ninja-devx/tree/main/examples/recipes) | HackSoft, Cosmic-lite and dishka architectures passing the same tests |
| [`saas`](https://github.com/ctolon/ninja-devx/tree/main/examples/saas) | multi-tenant tracker: roles, `ETag`/`If-Match`, cursors, throttles, field visibility, schemathesis |
| [`async_api`](https://github.com/ctolon/ninja-devx/tree/main/examples/async_api) | async-first orders: one thread hop per write, async dependencies, SSE |

Every option, parameter, setting and error code is listed with its type and default in the
[configuration reference](https://github.com/ctolon/ninja-devx/tree/main/docs/options), generated from the code.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md). Report vulnerabilities privately as described in
[SECURITY.md](SECURITY.md).

## License

[Apache License 2.0](LICENSE).

## Development

For the disposable Docker test stack and release gates, see [release validation](docs/project/releasing.md).

```bash
uv sync --group docs
uv run pytest                          # --update-snapshots to accept OpenAPI changes
uv run ruff check . && uv run ruff format --check .
uv run mypy && uv run pyright          # strict; tests/typing holds negative checks
for example in quickstart blog recipes saas async_api; do (cd examples/$example && uv run --project ../.. pytest -q); done
uv run python tools/generate_docs.py   # regenerate docs/options after changing options or docstrings
uv run mkdocs serve                    # DJANGO_SETTINGS_MODULE=tests.settings for the reference
uv run python benchmarks/overhead.py --check benchmarks/budget.json
```
