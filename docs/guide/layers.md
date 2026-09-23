# Services, repositories and other layers

!!! tip "Reference"

    Every option on this page, with types and defaults: [configuration reference](../options/dependency-injection.md).

A controller can do everything itself, and for many endpoints it should. When the
business logic grows, `ninja_devx.layers` gives you small, HTTP-free building blocks.
They never import Ninja, and none of them is required.

| Building block | What it is | HTTP knows about it through |
|---|---|---|
| `Repository[M]` | protocol: `get`, `add`, `change`, `remove`, `transaction()` | `service_class`, `Inject[...]` |
| `ModelRepository[M]` | the Django ORM implementation, sync and async | – |
| `ModelService[M]` | `create`/`update`/`delete` over a repository, sync or `.a` async | CRUD `service_class` |
| `Selector[F, M]` | a named read query | CRUD `selector_class` |
| `Policy[S, O]` | one authorization rule | `as_permission(policy, User)` |
| `RequestContext[U, T]` | the acting user, tenant and correlation ids | `request_context(User)` factory |
| `TaskQueue` | after-commit work (`django.tasks` or callables) | – |
| `DomainError` | business errors with an HTTP meaning | [Errors](errors.md) |

See the [architecture recipes](recipes.md) for complete apps in three styles.

## Services behind CRUD

```python
from ninja_devx.crud import CRUDController, ModelService
from ninja_devx.layers import dual


class PostService(ModelService[Post]):
    def __init__(self, repository: PostRepository, tasks: TaskQueue) -> None:
        super().__init__(repository)
        self.tasks = tasks

    @dual
    def create(self, data: Mapping[str, object]) -> Post:
        post = super().create({**data, "slug": slugify(str(data["title"]))})
        self.tasks.enqueue(notify_followers, post.pk)
        return post


class PostController(CRUDController[Post, PostOut, PostIn]):
    service_class = PostService
```

- Every write of the CRUD controller (`perform_create`, `perform_update`,
  `perform_destroy`, bulk operations) goes through `get_service(request)`.
- With a container, the service is resolved from it, so its `__init__` dependencies are
  injected. Without one, `service_class()` is called.
- Without `service_class`, a `ModelService(ModelRepository(model))` is used.
- The service receives plain data (`payload.model_dump()` plus the owner and parent).
  It never sees the request.
- `ModelService` wraps each write in `repository.transaction()`. `ModelRepository` uses
  `transaction.atomic()` and `InMemoryRepository` uses a no-op, so services are
  unit-testable without a database.

Overriding `perform_create` and friends still works for one-off cases.

## Repositories

```python
from ninja_devx.layers import ModelRepository


class PostRepository(ModelRepository[Post]):
    def queryset(self) -> QuerySet[Post]:
        return super().queryset().select_related("author")

    def published(self) -> QuerySet[Post]:
        return self.queryset().filter(status=Post.Status.PUBLISHED)
```

`get` raises `NotFound`. `add`/`change` run `full_clean()` (unless `validate=False`) and
raise `ValidationFailed` (422), set many-to-many relations, and save in a transaction.
The `a`-prefixed methods (`aget`, `aadd`, ...) are the async versions.

Register a repository by its protocol key when services should depend on the port rather
than the implementation:

```python
container.scoped(Repository[Post], PostRepository)
```

## `dual`: one method, sync and async callers

```python
class OrderService:
    @dual
    def place(self, command: PlaceOrder) -> Order: ...

    @place.native
    async def _place_async(self, command: PlaceOrder) -> Order: ...


service.place(command)  # sync
await service.place.a(command)  # native implementation, or the sync one in one thread hop
```

Provide `native` only when the async version really avoids blocking. Django's ORM is
synchronous underneath, so one thread hop for the whole unit of work is usually the
fastest option.

## Request context, without a request

```python
container.scoped(RequestContext[User, None], request_context(User))
container.scoped(RequestContext[User, Org], request_context(User, tenant=org_of))
```

`RequestContext` carries `user`, `tenant`, `request_id` (from `X-Request-ID`) and
`trace_id` (from `traceparent`). Services that take it work the same outside HTTP:

```python
with container.scope(
    {RequestContext[User, None]: RequestContext(user=admin, tenant=None)}
) as scope:
    scope.resolve(OrderService).cancel_expired()
```

`container.scope()` runs generator factory cleanups when it closes, like a request does.

## After-commit work

```python
from ninja_devx.layers import OnCommitTaskQueue, TaskQueue

container.singleton(TaskQueue, OnCommitTaskQueue)

self.tasks.enqueue(send_receipt, order.pk)  # a django.tasks task: enqueued after commit
self.tasks.call(cache.delete, f"order:{order.pk}")  # any callable
```

Inside a transaction the work runs on commit, and outside one it runs immediately.
`ImmediateTaskQueue` runs everything now. `RecordingTaskQueue` records calls for tests
(`.calls`, `.run_all()`).

### Celery, Dramatiq, RQ, Taskiq, Temporal and FastStream

`ninja_devx.contrib.tasks` adapts the same `TaskQueue` port to a task framework, so services
do not change. Celery (`.delay`/`.apply_async`), Dramatiq (`.send`) and Taskiq (`.kiq`) are
detected from the callable; RQ, Temporal and FastStream take a queue/client/broker:

```python
from ninja_devx.contrib.tasks import (
    CeleryTaskQueue,
    DeferredTaskQueue,
    FastStreamTaskQueue,
    RQTaskQueue,
    TemporalTaskQueue,
)
from ninja_devx.layers import TaskQueue

container.singleton(TaskQueue, CeleryTaskQueue())
# or: RQTaskQueue(redis_queue), TemporalTaskQueue(temporal_client),
#     FastStreamTaskQueue(broker, queue="tasks")
```

The adapter is a drop-in for `OnCommitTaskQueue` for every `TaskQueue` consumer; the
`enqueue`/`call` methods are the only contract. `DeferredTaskQueue(defer_fn)` adapts any
``defer(function, args, kwargs)`` callable when there is no framework-specific adapter.

## Policies

```python
class CanCancel:
    def allows(self, subject: User, order: Order, /) -> bool:
        return order.owner_id == subject.pk and order.status == "placed"


@post("/{pk}/cancel", permissions=Also(as_permission(CanCancel(), User)))
def cancel(self, request: HttpRequest, order: Instance[Order]) -> Order: ...


require(CanCancel(), context.user, order)  # in a service: PolicyDenied → 403
```

The same rule guards the endpoint (as an object permission) and the service.
`as_permission(policy, User)` uses the authenticated user as the subject. Pass a
function of the request (and `asubject=` for async) to use something else, such as a
membership.

## Selectors for lists

```python
class LongNotes:
    def __call__(self, filters: NoteFilters, /) -> QuerySet[Note]:
        return Note.objects.annotate(length=Length("text")).filter(length__gt=100)


class NoteController(ReadOnlyModelController[Note, NoteOut]):
    filter_schema = NoteFilters
    selector_class = LongNotes
```

A controller selector must return a QuerySet for its model. Its results are intersected
with `scoped_queryset(request)`, preserving tenant, owner, parent, object-permission,
soft-delete and custom `get_queryset` restrictions. Ordering, pagination and query
optimizations still apply. Materialized lists are rejected because they cannot preserve
these database-level security guarantees. The same restrictions apply to export.
The selector is resolved from the container when one is configured.

## Use cases

When an operation is only "map the payload to a command and run the handler", skip the
method body:

```python
class Orders(Controller):
    place = use_case(
        post("/", response={201: OrderOut}),
        PlaceOrderHandler,
        command=OrderIn.to_command,
        status=201,
    )
```

The handler is resolved from the container, and an `async def __call__` makes the
operation async. For path parameters or extra logic, write the method and take the
handler as a parameter: `handler: Inject[CancelOrderHandler]`.

## Testing the layers

```python
from ninja_devx.layers.testing import InMemoryRepository, make_context

products = InMemoryRepository(Product)
lamp = products.add({"name": "Lamp", "stock": 3})
handler = PlaceOrderHandler(products, InMemoryRepository(Order), make_context(ada, None))
```

`capture_commits()` (or the `captured_commits` fixture) runs on-commit callbacks at the
end of a test transaction. See [Testing](testing.md).

## Method and configuration reference

Every class on this page imports from `ninja_devx.layers`. The CRUD aliases `Repository`,
`ModelRepository` and `ModelService` are also re-exported from `ninja_devx.crud`. The
[API reference](../reference.md) renders the docstrings of each module below.

### `Repository[M]` (protocol)

The persistence port a service depends on. `AsyncRepository[M]` is the async twin with the
same methods prefixed with `a` (`aget`, `aadd`, `achange`, `aremove`).

| Method | Signature | Returns | Raises |
|---|---|---|---|
| `get` | `get(lookup, /, *, field="pk")` | the matching `M` | `NotFound` (404 when mapped) |
| `add` | `add(data, /)` | the created `M` | `ValidationFailed` (422) |
| `change` | `change(instance, data, /)` | the updated `M` | `ValidationFailed` (422) |
| `remove` | `remove(instance, /)` | `None` | — |
| `transaction` | `transaction()` | the unit-of-work context manager | — |

`ModelService` opens `transaction()` around every write, so an implementation returns
`transaction.atomic()` (ORM, `ModelRepository`) or `contextlib.nullcontext()` (in-memory
fakes). `field` selects the lookup column and may be a related path such as `"author_id"`.

### `ModelRepository[M]` (ORM)

```python
from ninja_devx.layers import ModelRepository


class PostRepository(ModelRepository[Post]):
    def queryset(self) -> QuerySet[Post]:
        return super().queryset().select_related("author")
```

| Parameter | Default | Meaning |
|---|---|---|
| `model` | the `ModelRepository[...]` argument | model to persist; required when the subclass is not parameterized |
| `validate` | `True` | run `Model.full_clean()` before `save()`; failures become `ValidationFailed` |
| `using` | `None` (write router) | database alias for every read, write and `transaction()` |

`add`/`change` delegate to `ninja_devx.layers.persistence.save_instance`, which assigns
fields (foreign keys accept an instance or a primary key), sets many-to-many values after
the insert and wraps the work in a transaction (a savepoint when nested). `remove` calls
`instance.delete(using=...)`. The async methods use Django's async ORM for `aget` and a
single thread hop for writes.

### `ModelService[M]` (business operations)

| Method | Signature | Wrapped in |
|---|---|---|
| `create` | `create(data, /)` | `repository.transaction()` |
| `update` | `update(instance, data, /)` | `repository.transaction()` |
| `delete` | `delete(instance, /)` | `repository.transaction()` |

The constructor takes `repository=None, *, model=None`; without a repository it builds a
`ModelRepository` for the model. Each method is decorated with `@dual`, so
`await service.create.a(data)` runs a native `@create.native` implementation when one is
registered, otherwise the sync body in one thread hop.

### `Selector[Filters, M]` (protocol)

`__call__(filters, /) -> QuerySet[M] | Sequence[M]`. A standalone selector may return a
list; a CRUD `selector_class` must return a `QuerySet` for the controller model, because
the controller intersects it with `scoped_queryset(request)` (tenant, owner, parent,
object permission, soft delete) and then orders, paginates and optimizes it.

### `Policy`, `allowed` and `require`

```python
from ninja_devx.layers import PolicyDenied, allowed, require

assert allowed(CanCancel(), user, order)
require(CanCancel(), user, order)  # PolicyDenied (403) when the rule says no
```

`Policy[S, O]` is `allows(subject, obj, /) -> bool`. Pass the same object to
`as_permission(policy, Subject)` to guard HTTP endpoints. `require` uses a `message`
attribute on the policy for the error detail when one is present.

### `RequestContext[User, Tenant]`

| Field | Default | Meaning |
|---|---|---|
| `user` | required | the acting user |
| `tenant` | required (`None` allowed) | the acting tenant |
| `request_id` | `uuid4().hex` | correlation id (`X-Request-ID` or generated) |
| `trace_id` | `None` | W3C trace id from `traceparent` |
| `metadata` | `{}` | free-form string metadata for logs and audit |

Build one directly for tasks and commands, or register the factory with
`container.scoped(RequestContext[User, None], request_context(User))`. The context is
frozen and slotted, so it is safe to share across the request scope.

### `TaskQueue` implementations

| Class | Constructor | Behaviour |
|---|---|---|
| `OnCommitTaskQueue` | `using=None` | runs on the transaction commit, or immediately outside one |
| `ImmediateTaskQueue` | — | runs work synchronously |
| `RecordingTaskQueue` | — | records `(task_or_function, args, kwargs)` in `.calls`; `.run_all()` drains them in order |

The protocol has two methods: `enqueue(task, *args, **kwargs)` for `django.tasks` tasks and
`call(function, *args, **kwargs)` for any callable. `after_commit(function, *, using=None)`
is the free function behind `OnCommitTaskQueue`.

### `DomainError` and `HttpMappable`

| Class | `http_status` | `code` | Default `detail` |
|---|---|---|---|
| `DomainError` | 400 | `domain_error` | the docstring |
| `NotFound` | 404 | `not_found` | `Not found.` |
| `PermissionDenied` | 403 | `permission_denied` | `You do not have permission to perform this action.` |
| `Conflict` | 409 | `conflict` | `The request conflicts with the current state.` |
| `ValidationFailed` | 422 | `validation_failed` | `Validation failed.` |

`DomainError(message, **details)` merges `details` into the response body;
`ValidationFailed(message, errors={field: [message]})` produces Ninja's 422 list shape.
Any exception with class attributes `http_status` and `code` and an `error_body()` method
satisfies `HttpMappable`, so `ErrorMap` can map your own errors without subclassing.
