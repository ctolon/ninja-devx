---
hide:
  - navigation
---

# ninja-devx

Typed controllers and reusable resource policies for Django Ninja. The package
centralizes recurring tenant/owner scoping, transaction, permission, dependency-lifetime
and error-handling rules while keeping Django models and Ninja schemas.

Read the [scope and engineering rationale](project/scope.md) for adoption criteria,
responsibility boundaries and tradeoffs. Version 0.0.2 is an alpha release; the
[support policy](project/support.md) describes the compatibility contract.

[Get started](getting-started/quickstart.md){ .md-button .md-button--primary }
[See the examples](getting-started/examples.md){ .md-button }
[Compare](project/comparison.md){ .md-button }

```python
class ProjectController(
    SoftDeleteMixin[Project, ProjectOut], CRUDController[Project, ProjectOut, ProjectIn]
):
    tenant_field = "workspace"  # every query scoped to the caller's workspace
    soft_delete = SoftDelete("archived_at", deleted_by="archived_by")
    etag = ETag(field="updated_at")  # 304 on reads, If-Match → 412 on writes
    search_fields = ("name",)
    ordering_fields = ("name", "updated_at")
    routes = {"restore": {"path": "/{pk}/unarchive", "permissions": [IsWorkspaceAdmin()]}}


mount(api, {"/projects": ProjectController}, prefix="/v1")
```

These lines give you list, retrieve, create, update, partial update, archive and unarchive
endpoints, with:

- tenant isolation
- search and enum-checked ordering
- conditional requests and optimistic locking
- admin-only unarchiving
- relation loading derived from supported schema fields
- declared errors in the generated OpenAPI schema

<div class="grid cards" markdown>

-   **Controllers on native routers**

    ---

    `@get/@post` on classes, typed options, scopes, hooks, plugins. `as_router()` returns
    a `ninja.Router`; compatibility tests cover the operation integration.

    [Controllers](guide/controllers.md)

-   **Reusable CRUD policy**

    ---

    Generic arguments configure filters, search, ordering, cursor pagination, nested
    resources, bulk writes, soft delete and N+1 prevention.

    [CRUD](guide/crud.md)

-   **Layers without force**

    ---

    HTTP-free services, repositories, policies and domain errors, when you need them.
    Three architecture recipes pass the same tests.

    [Services and layers](guide/layers.md)

-   **CQRS without a bus**

    ---

    `use_case` and `use_query` handlers, `DomainEvent` delivered after commit through an
    explicit `EventBus`, and `UnitOfWork` for multi-repository transactions.

    [CQRS and DDD](guide/cqrs.md)

-   **SaaS-ready HTTP**

    ---

    `tenant_field`, `ETag`/`If-Match`, user, scope and tenant throttles, role-based field
    visibility, and middleware for security headers, request hardening, response caching
    and pagination headers.

    [Multi-tenancy](guide/tenancy.md)

-   **Async that stays cheap**

    ---

    One class serves sync and async. A unit of work takes one thread hop, and lazy loads
    and blocking calls fail loudly.

    [Async and sync](guide/async.md)

-   **Contract and drift checks**

    ---

    Scaffolding with model constraints, schema drift checks, system checks, schemathesis
    contract tests, and typed TypeScript and pydantic clients.

    [Testing](guide/testing.md)

-   **Inspect and diagnose**

    ---

    `devx_inspect` prints the resolved policy of a mounted controller, and
    `@requires_related` makes N+1 loading explicit.

    [Inspecting controllers](guide/inspect.md)

</div>

The [guides index](guide/index.md) maps each task to its page, and
[Controllers](guide/controllers.md), [CRUD](guide/crud.md),
[Permissions](guide/permissions.md) and [Errors](guide/errors.md) are the best starting
points.

## Principles

Native Ninja
:   Controllers compile into regular `ninja.Router`s. Parsing, validation, auth,
    pagination, throttling, streaming and exception handlers are Ninja's own.

Typed end to end
:   Options are `TypedDict`s and generic arguments configure CRUD. mypy strict and
    pyright strict pass, and the package does not use `typing.Any`.

Fail at startup
:   Configuration mistakes raise `ControllerConfigError` from `as_router()`, or show up
    in `manage.py check`, not on the first request.

No hidden state
:   Request data is never stored on `self`, and there are no global registries.

Measured
:   Controllers cost microseconds over function views, and an optional local budget check
    guards that line. See [Performance](project/performance.md).
