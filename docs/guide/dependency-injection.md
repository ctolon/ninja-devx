# Dependency injection

!!! tip "Reference"

    Every option on this page, with types and defaults: [configuration reference](../options/dependency-injection.md).

`as_router(container=...)` accepts any object implementing one of these protocols:

| Protocol | Method | Used for |
|---|---|---|
| `Resolver` | `resolve(cls)` | singleton controllers, simple adapters |
| `RequestScopeProvider` | `request_scope(request)` → context manager | per-request scopes |
| `AsyncRequestScopeProvider` | `arequest_scope(request)` → async context manager | async operations |
| `CheckableContainer` | `check(key, asynchronous=...)` | startup validation (optional) |

Without a container, controllers are built with `cls()`, and a required `__init__`
parameter is a startup error.

## Two ways to receive dependencies

```python
class OrderController(Controller):
    def __init__(self, orders: OrderService) -> None:  # every operation uses it
        self.orders = orders

    @post("/{pk}/refund")
    def refund(self, request: HttpRequest, pk: int, payments: Inject[PaymentGateway]) -> Refund:
        return payments.refund(self.orders.get(pk))  # only this operation needs it

    @get("/whoami")
    def whoami(
        self, request: HttpRequest, ip: Annotated[str, Resolve(client_ip)]
    ) -> dict[str, str]:
        return {"ip": ip}
```

- `Inject[T]` resolves `T` from the controller's container for this call.
- `Annotated[T, Resolve(fn)]` passes `fn(request)`, where `fn` may be `async def` in
  async operations.
- Neither appears in OpenAPI.
- `as_router()` checks that the container can build every `Inject[T]`.
- Operation-level injection keeps constructors small, and lets a `Scope.SINGLETON`
  controller use request-scoped services.
- `self.resolve(request, Key)` is the imperative form.

## Built-in container

```python
container = Container()
container.singleton(Settings)
container.singleton(PostRepository, DjangoPostRepository)  # abstract keys are fine for mypy
container.scoped(UnitOfWork)  # one per request (or container.scope())
container.transient(Clock, make_clock)
container.instance(Config, Config.from_env())
container.scoped(User, authenticated_user(User))
container.scoped(RequestContext[User, None], request_context(User))
```

- **Autowiring:** concrete classes are built from their `__init__` annotations, and
  `Annotated[T, ...]` resolves `T`.
- **Factories:** a factory may be a class, a function, a generator (code after `yield`
  runs when the scope closes), or `async def` and async generator versions of those.
  Async factories are resolved by async operations (`aresolve`).
- **Defaults:** parameters with defaults are injected only when registered;
  `X | None = None` counts as `X`.
- **Generics:** generic keys are substituted, so `ModelService[Post]` asks for
  `Repository[Post]`.
- **Request:** `HttpRequest` is injectable in request scopes.
- **Scopes outside HTTP:** `with container.scope({RequestContext[User, None]: ctx}) as scope:`
  gives tasks and commands the same scoped services, and cleanups run when it closes.
- **Startup validation:** `container.check(Key)` runs at `as_router()`. It catches
  unresolvable keys, cycles (with the chain), captive dependencies (a singleton depending
  on something scoped), and async-only factories needed by sync operations.
- **Speed:** plans are cached, so resolving costs microseconds.
- **Tests:** `with container.override(Key, fake): ...` swaps a dependency.
- **Cleanup:** `container.close()` / `await container.aclose()` finalizes singletons.

## dishka

```python
from ninja_devx.contrib.dishka import DishkaResolver, provide_controllers

provider = AppProvider()
provide_controllers(provider, [PostController, CommentController])
resolver = DishkaResolver(make_container(provider), async_container=make_async_container(provider))

mount(api, {"/posts": PostController}, container=resolver)
```

- A dishka scope (`Scope.REQUEST` by default) opens per request, with `HttpRequest` in
  its context.
- Sync operations use the sync container. Async operations use `async_container` when
  given, and otherwise the sync container in a worker thread.
- `as_router()` fails when dishka cannot provide the controller or an `Inject[...]`
  dependency.
- `AsyncDishkaResolver` is the async-only variant.

## svcs

```python
from ninja_devx.contrib.svcs import SvcsResolver

PostController.as_router(container=SvcsResolver(registry))
```

Both adapters run cleanups when the request ends.
