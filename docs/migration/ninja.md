# Migrating from Django Ninja

Existing Ninja routers and ninja-devx controllers can share the same `NinjaAPI`. Adopt
controllers where repeated resource policy is becoming difficult to maintain; a function
that already expresses its operation clearly does not need to move.

The migration keeps Ninja schemas, auth callables, API exception handlers and Django models.
It changes how handlers are grouped and where common policies execute. Ninja's
[router guide](https://django-ninja.dev/guides/routers/) describes the underlying mounting
model; controllers produce routers for that same API.

## 1. Move a function without changing its contract

Before:

```python
from django.http import HttpRequest
from ninja import Router, Schema


class HealthOut(Schema):
    ok: bool


router = Router()


@router.get("/health", response=HealthOut, operation_id="health_read", auth=None)
def health(request: HttpRequest):
    return {"ok": True}
```

After:

```python
from django.http import HttpRequest
from ninja import NinjaAPI, Schema
from ninja_devx import Controller, get


class HealthOut(Schema):
    ok: bool


class HealthController(Controller):
    @get("/health", response=HealthOut, operation_id="health_read", auth=None)
    def health(self, request: HttpRequest):
        return {"ok": True}


api = NinjaAPI()
api.add_router("", HealthController.as_router())
```

Preserve explicit operation IDs when generated clients use them. The controller's default
ID derives from its class/method and may differ from the old function's ID. Keep the old
route's methods, slash policy, response status map, auth and throttles explicit during
this first step. Do not register both implementations at the same path/method.

## 2. Move shared configuration after parity tests pass

Common auth and permission settings can live in `ControllerOptions`:

```python
from ninja.security import django_auth
from ninja_devx import Controller, ControllerOptions, IsAuthenticated, get


class ProfileController(Controller):
    options = ControllerOptions(auth=django_auth, permissions=[IsAuthenticated()])

    @get("/", response=dict[str, str])
    def profile(self, request):
        return {"username": request.user.get_username()}
```

Existing API-level auth still applies unless explicitly replaced. `auth=None` is a public
operation, not “inherit authentication.” Review inherited and route-specific policy before
removing repeated arguments. Authentication is performed by Ninja; ninja-devx permissions
run after validated arguments are available. Consequently malformed and forbidden requests
may be rejected at different stages than an old hand-written check. Test the observed
status and disclosure behavior.

Ninja auth and request-user handling remain separate concepts for custom bearer/API-key
implementations. An arbitrary object returned as `request.auth` is not automatically a
Django user. Follow the [authentication guide](https://django-ninja.dev/guides/authentication/)
and explicitly establish the principal your permissions expect.

## 3. Replace repeated CRUD code where it helps

```python
from typing import Annotated

from ninja import Schema
from ninja_devx.crud import CRUDController
from pydantic import Field

from notes.models import Note


class NoteIn(Schema):
    title: Annotated[str, Field(min_length=1, max_length=200)]
    body: str = ""


class NoteOut(Schema):
    id: int
    title: str
    body: str
    done: bool


class Notes(CRUDController[Note, NoteOut, NoteIn]):
    owner_field = "owner"
    scope_queryset_to_owner = True
    search_fields = ("title",)
    ordering_fields = ("id", "title")
```

Mount with `api.add_router("/notes", Notes.as_router())`. The
[Quickstart example](../getting-started/quickstart.md) supplies the corresponding model,
URLconf and tests. Built-in routes are `/`, `/{pk}` and their documented methods. Use the
`routes` mapping to preserve an existing path; compare OpenAPI and URL reverse names after
registration.

The generic controller introduces behavior your old functions may not have had: model
validation, generated filters/order validation, configured scope and transactional writes.
It also separates input from output. Reuse an existing schema only when it has the intended
write permissions; response-only fields should not become writable by convenience.

## 4. Translate endpoint responsibilities

| Function-view responsibility | Controller equivalent | Important boundary |
|---|---|---|
| `Model.objects.filter(owner=request.user)` | Owner field plus owner queryset scoping | Owner checks alone do not filter lists |
| Fetch, then check a permission | `Instance[Model]` or `get_object` | A custom raw ORM lookup still needs a check |
| `transaction.atomic(using=...)` | Built-in write scope or explicit atomic service | External HTTP/storage effects are not rolled back |
| Repeated service construction | Constructor or `Inject[T]` with a registered container | Select singleton/request/transient lifetime explicitly |
| Error-to-response try/except | ErrorMap / DomainError | Compare old status and body before adopting defaults |
| Model input conversion | Explicit schema plus `perform_create/update` | Preserve custom validation, context fields and alias |
| Query optimization | Explicit queryset hints plus schema planner | Arbitrary resolver access cannot be inferred |
| Pagination decorator | `pagination_class` / options on model controllers | Preserve page size and envelope if clients depend on them |
| Decorators using function signatures | Operation decorators/plugins | Recheck composition and registration-time introspection |

## 5. Migrate sync and async independently

An existing async function can become an async method without enabling async CRUD globally.
For generic model controllers, choose `mode="sync"`, `"async"` or the configured default.
Await network I/O; put a transactional group of ORM writes in one sync boundary. Turning a
method into `async def` does not make a sync service non-blocking.

The [Ninja async guide](https://django-ninja.dev/guides/async-support/) covers the underlying
framework model. The [ninja-devx async guide](../guide/async.md) adds dependency and transaction
patterns. Test async request cancellation, generator cleanup and lazy relation access.
Async stream auth/permission/binding preflight happens before headers; an exception raised
later by the generator cannot replace an already-sent status.

## 6. Keep dependency lifetimes visible

A request parameter, a request-scoped service and a singleton service have different
owners. Do not move `request`, a tenant or a database transaction onto `self` when a
controller can be singleton-scoped. Store per-request data in method arguments, the
invocation context or a request-scoped dependency.

Resource-producing factories must have matching cleanup. Test a successful request, an
exception and an async disconnect. Existing module-level client instances can stay in an
application-owned singleton container if they are safe to share and closed at shutdown;
there is no need to construct every connection on every request.

## 7. Acceptance and rollout

Keep a Ninja TestClient test for every converted route and a real Django WSGI/ASGI test for
middleware-sensitive behavior. Compare status/body/headers, auth, 204 bodies, pagination,
PATCH omission/null handling, multi-tenant visibility and side effects. Use query assertions
for nested schemas and snapshot operation IDs before regenerating clients.

Retain other function routers while one controller is evaluated. The rollback can be a
URLconf switch if models and request/response contracts remain compatible. Contrib modules
are separate adoption decisions: moving functions to a controller does not require audit,
webhooks, uploads or a new DI framework.
