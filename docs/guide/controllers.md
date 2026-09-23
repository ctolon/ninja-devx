# Controllers

!!! tip "Reference"

    Every option on this page, with types and defaults: [configuration reference](../options/controllers.md).

```python
from ninja_devx import Controller, ControllerOptions, Scope, get, post


class UserController(Controller):
    options = ControllerOptions(tags=["users"], auth=django_auth)

    def __init__(self, service: UserService) -> None:
        self.service = service

    @get("/", response=list[UserOut])
    def list_users(self, request: HttpRequest) -> list[User]:
        return self.service.list()

    @post("/", response={201: UserOut})
    async def create(self, request: HttpRequest, payload: UserIn) -> Status[User]:
        return Status(201, await self.service.create(payload))


api.add_router("/users", UserController.as_router(container=container))
```

## Operations

`@get`, `@post`, `@put`, `@patch`, `@delete` and `@api_operation(methods, path)` take all
of `Router.api_operation`'s options. They also take these ninja-devx options:

| Option | Effect |
|---|---|
| `permissions` | replaces the controller's permissions; `Also(...)` adds to them ([Permissions](permissions.md)) |
| `decorators` | wrapping view decorators such as `paginate(...)`; first item is outermost |
| `hooks` | operation hooks, inside the controller's ([Hooks](hooks.md)) |
| `atomic`, `database` | run in `transaction.atomic()` (`"durable"` supported; sync operations) |
| `errors` | exception → response rules over the controller's ([Errors](errors.md)) |
| `raises` | exceptions to document in OpenAPI; each must have a rule |
| `meta` | typed metadata for permissions and hooks (`operation.meta(Kind)`) |
| `document_errors` | document 401/403/404/422 in OpenAPI (default on) |

- The first parameters must be `(self, request)`; `request` may be any `HttpRequest`
  subclass, e.g. `AuthedRequest[User]`.
- Parameters work as in function views. `from __future__ import annotations` is supported.
- Sync, async, generator and async-generator (streaming) methods are supported
  ([Async and sync](async.md)).
- Extra parameters can be injected instead of parsed: `Inject[T]`, `Annotated[T, Resolve(fn)]`,
  `Instance[Model]` ([Dependency injection](dependency-injection.md)).
- Stacking `@get("/a")` and `@get("/b")` exposes one method under both paths.
- Default `operation_id` is `<snake_case class>_<method>`.
- Routes are registered static-segments-first: `/me` and `/bulk` always match before
  `/{pk}`, whatever the declaration order.

## Options

`options: ClassVar[ControllerOptions]` sets defaults for every operation. They merge in
this order, and later layers win:

1. `NINJA_DEVX["DEFAULT_OPTIONS"]`
2. `options` along the MRO
3. `as_router(**options)`
4. operation options

Keys: `auth`, `throttle`, `tags`, `permissions`, `decorators`, `hooks`, `atomic`,
`database`, `errors`, `meta`, `plugins`, `allow_mixed_path`, `deprecated`,
`document_errors`, `by_alias`, `exclude_unset`, `exclude_defaults`, `exclude_none`,
`operation_id_prefix`, `url_name_prefix`.

Permissions and hooks from different layers are not concatenated: the closest layer
replaces them, unless it uses `Also(...)`.

A shared base class is the usual way to set project conventions:

```python
class PrivateController(Controller):
    options = ControllerOptions(auth=JWTAuth(), permissions=[IsAuthenticated()])
```

## Scopes

| Scope | Instance |
|---|---|
| `Scope.REQUEST` (default) | one per request, built inside the container's request scope |
| `Scope.SINGLETON` | one per `as_router()` call, built eagerly (stateless controllers) |

`as_router()` returns an independent router every time, so a controller can be mounted
several times with different containers, scopes or options.

## Lifecycle

```python
class AuditedController(Controller):
    def before_operation(self, request: HttpRequest, operation: OperationInfo) -> None:
        audit.start(operation.operation_id)

    def after_operation(
        self, request: HttpRequest, operation: OperationInfo, result: object
    ) -> object:
        return result
```

`before_operation` runs after permissions and bound parameters; both may be `async def`
on async operations. Overriding neither costs nothing.

## Extension points

| Class method | Use |
|---|---|
| `customize_operation(name, spec)` | rewrite an operation (paths, options) in reusable bases |
| `operation_bindings(name, spec)` | add hidden parameters (nested resources use it) |
| `documented_errors(name, spec)` | add documented error codes |

## Responses: status, headers and cookies

These use Ninja's own mechanisms, which work unchanged in controllers:

```python
@post("/", response={201: ItemOut})
def create(self, request: HttpRequest, payload: ItemIn, response: HttpResponse) -> Status[Item]:
    item = self.items.create(payload)
    response["Location"] = f"/items/{item.pk}"  # Ninja's temporal response
    response.set_cookie("last_item", str(item.pk))
    return Status(201, item)
```

Document response headers with `openapi_extra={"responses": {201: {"headers": {...}}}}`.
Use an integer status key so it merges with the generated response. Ninja validates
return values against `response=` in every mode.

## Use cases

`use_case(post("/"), Handler, command=Schema.to_command)` declares an operation that
maps the payload to a command and calls a handler resolved from the container. See
[Layers](layers.md#use-cases).

## Plugins

```python
class TenantHeader:
    def on_operation(
        self, controller: type[Controller], name: str, spec: OperationSpec, /
    ) -> OperationSpec:
        return spec.with_options(openapi_extra={"parameters": [TENANT_PARAMETER]})

    def bindings(
        self, controller: type[Controller], name: str, spec: OperationSpec, /
    ) -> Sequence[ParameterBinding]:
        return ()


mount(api, ROUTES, plugins=[TenantHeader()])  # or NINJA_DEVX["PLUGINS"]
```

A plugin rewrites operations, adds hidden parameters to every controller it is given,
and may define `checks(controller)` for [system checks](checks.md). Plugins are listed
explicitly; there is no entry-point discovery.

## Seeing what runs

[`devx_inspect`](inspect.md) prints the controller's resolved options, scoping,
permissions, relations and operations, which is the fastest way to confirm what a
configuration actually produced.

```bash
python manage.py devx_inspect app.api.PostController
```
