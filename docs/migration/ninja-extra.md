# Migrating from Django Ninja Extra

Both projects provide class-based organization for Ninja APIs, but their controller,
permission and dependency interfaces differ. There is no drop-in adapter. Keep the existing
Extra API at its current prefix while a plain `NinjaAPI` serves the migrated resources at
a temporary prefix.

The upstream [controller documentation](https://eadwincode.github.io/django-ninja-extra/api_controller/)
and [model-controller guide](https://eadwincode.github.io/django-ninja-extra/api_controller/model_controller/)
are the reference for the old interface. Confirm your installed version's configuration;
application subclasses may add behavior beyond these examples.

## 1. Inventory inherited behavior

Read the controller's decorators, base classes, `ModelConfig`, model schema settings,
permission classes and Injector modules. Record generated routes as well as methods you
wrote. Inspect the resulting OpenAPI and real requests rather than assuming two similarly
named controllers generate the same contract.

Pay attention to controller discovery, automatic model schemas, route enable/disable
configuration, authentication imports, permission messages, pagination decorators and
custom services. These are the main sources of differences during a port.

## 2. Convert one explicit route

A small Extra controller:

```python
from ninja import Schema
from ninja_extra import NinjaExtraAPI, api_controller, route


class StatusOut(Schema):
    status: str


@api_controller("/status")
class StatusController:
    @route.get("/", response=StatusOut, operation_id="status_read")
    def read(self):
        return {"status": "ok"}


api = NinjaExtraAPI()
api.register_controllers(StatusController)
```

The corresponding ninja-devx controller:

```python
from django.http import HttpRequest
from ninja import NinjaAPI, Schema
from ninja_devx import Controller, get


class StatusOut(Schema):
    status: str


class StatusController(Controller):
    @get("/", response=StatusOut, operation_id="status_read")
    def read(self, request: HttpRequest):
        return {"status": "ok"}


api = NinjaAPI(urls_namespace="migrated-status")
api.add_router("/status", StatusController.as_router())
```

The prefix moves from the controller decorator to mounting. The explicit operation ID
avoids an incidental generated-client rename. Request state is a parameter, not
`self.context.request`. A class definition does not register itself; mount every controller
explicitly or through `mount`.

## 3. Translate model configuration explicitly

| Extra configuration/concept | ninja-devx surface | Work required |
|---|---|---|
| `ModelControllerBase` with ModelConfig | `CRUDController[Model, Out, In]` or individual mixins | List every generated operation and its response/status |
| Model schema configuration | Explicit Ninja schemas or opt-in AutoCRUDController | Review read/write fields, required values, nulls and constraints |
| Controller prefix/decorator | API/router mounting | Preserve URL namespaces, slashes and tags |
| Route decorators | `get/post/put/patch/delete` | Preserve response map, auth, explicit IDs and custom options |
| Controller context request | Method request argument | Remove per-request mutation of shared instances |
| Object lookup helper | `get_object(request, pk)` / `Instance[Model]` | Preserve queryset restriction and object checks |
| Searching/ordering decorators | `search_fields`, `ordering_fields`, FilterSchema | Match parameter names, supported lookups and invalid values |
| Pagination decorator | `pagination_class` and `pagination_options` | Match response envelope, count and page limits |
| Injector provider/module | Container factory or dishka/svcs adapter | Port scope and cleanup, not just constructor annotations |
| Extra permission class | ninja-devx BasePermission implementation | Reimplement request/object methods against the new interface |
| Custom model service | `service_class` or `perform_*` hook | Preserve transactions, validation and request-controlled fields |

Do not replace a ModelConfig with `AutoCRUDController[Model]` until its generated input and
output match the old field policy. Explicit schemas are usually easier to review during
the migration. The [CRUD guide](../guide/crud.md) lists defaults and override points.

## 4. Port permissions and scope

Extra permission objects are not interchangeable with ninja-devx permissions. Translate
business rules and exercise anonymous, authenticated, wrong-owner and wrong-tenant cases.
In ninja-devx, permission instances are shared configuration; they must not store the
current request or object. Sync and async checks have documented method pairs.

For an owner-controlled resource:

```python
from ninja_devx.crud import CRUDController

# Model and schemas are defined by the application.
class Documents(CRUDController[Document, DocumentOut, DocumentIn]):
    owner_field = "owner"
    scope_queryset_to_owner = True
    tenant_field = "workspace"
```

This configuration assumes an authorized workspace resolver. It does not make an arbitrary
`X-Workspace` value trustworthy. Owner/object checks and list visibility are different
policies. Optional `ObjectPermissions` can filter by view grants; built-in writes then
recheck the persisted result before commit.

A route's permission override can replace inherited permissions. Use `Also(...)` for an
intentional addition, and test composed request/object checks. Existing services that
construct objects with another owner or tenant must be corrected; disabling response
refresh does not disable the scope check.

## 5. Port Injector bindings by lifetime

Suppose the application depends on a shared configuration object and a request-scoped
service. Register those lifetimes explicitly:

```python
from ninja_devx import Container, Controller, get


class Settings:
    pass


class ReportService:
    def __init__(self, settings: Settings):
        self.settings = settings


class Reports(Controller):
    def __init__(self, service: ReportService):
        self.service = service

    @get("/", response=dict[str, bool])
    def read(self, request):
        return {"available": self.service is not None}


container = Container()
container.singleton(Settings)
container.scoped(ReportService)
router = Reports.as_router(container=container)
```

The application still owns container shutdown. Resource factories should yield and close
resources through the documented lifecycle. Override providers in tests rather than
replacing global objects. Do not keep a request-scoped dependency in a singleton controller;
startup checks and lifecycle tests should cover that combination. See
[dependency injection](../guide/dependency-injection.md).

## 6. Check persistence and HTTP differences

Default model writes validate the model and execute on the controller's selected write
alias. Custom ModelRepository instances with conflicting aliases are rejected. A service
that writes to a separate store remains responsible for consistency; no transaction spans
an HTTP API and a relational database automatically.

Ninja schemas can often be reused, but audit their resolvers and aliases. Invalid input,
ordering, domain errors and model validation may produce different error codes or shapes.
Compare list envelopes and async pagination behavior. Preserve explicit operation IDs
until client regeneration is planned.

Async handlers do not require an immediate whole-controller conversion. Move them with
existing behavior first, then adopt generic async variants or DI cleanup separately.
Async preflight uses a narrow Ninja operation integration; supported version tests are
still required. Neither a plain router nor a class-based API can guarantee immunity to
upstream framework changes.

## 7. Remove the old integration after parity

Run accepted/rejected request fixtures against both prefixes, including middleware and
session/CSRF behavior through Django's HTTP handlers. Verify custom action permissions,
query counts, transactions, cancellation and dependency teardown. Confirm generated client
and OpenAPI diffs with the consuming application.

When the migrated prefix is accepted, move its routes to the intended public paths and
remove duplicate controller registration. Remove `ninja_extra` from INSTALLED_APPS and the
old Injector wiring only when no remaining routes use them. Keep both implementations
available during rollback if their database and side-effect contracts remain compatible.
The [plain Ninja migration guide](ninja.md) covers coexistence with remaining function
routers; [DRF migration](drf.md) covers serializer-heavy ports.
