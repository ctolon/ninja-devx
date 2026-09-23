# CQRS and DDD

!!! tip "Reference"

    Every option on this page, with types and defaults: [configuration reference](../options/controllers.md#cqrs-building-blocks).

`ninja_devx.cqrs` adds small, optional pieces for CQRS and DDD over
[Services and layers](layers.md). They are conventions with types, not a framework: there is
no command bus, no global registry and no automatic wiring.

| Concept | Building block |
|---|---|
| Command (write) | a frozen dataclass + `use_case(...)` |
| Query (read) | a frozen dataclass + `use_query(...)` |
| Handler | a class with `__call__(message)`, resolved from the container |
| Domain event | `DomainEvent` + `EventBus` (delivered after commit) |
| Unit of work | `UnitOfWork` (several repositories, one transaction) |
| Marker | `Command`/`Query` (optional base classes, both derive from `Message`) |

## Commands and queries

`use_case` binds a write operation to a handler; `use_query` is the read counterpart. Both
map the validated request to a message and resolve the handler from the controller's
container. See [Use cases](layers.md#use-cases) for the full mechanism.

```python
from dataclasses import dataclass

from ninja import FilterSchema
from ninja_devx import Controller, Inject, get, post, use_case, use_query
from ninja_devx.cqrs import Command, Query
from ninja_devx.layers import Repository


@dataclass(frozen=True, slots=True)
class PlaceOrder(Command):
    product_id: int
    quantity: int


@dataclass(frozen=True, slots=True)
class OrderStats(Query):
    prefix: str


class PlaceOrderHandler:
    def __init__(self, products: Repository[Product], orders: Repository[Order]) -> None:
        self.products, self.orders = products, orders

    def __call__(self, command: PlaceOrder) -> Order: ...


class OrderStatsHandler:
    def __call__(self, query: OrderStats) -> dict[str, int]: ...


class StatsFilters(FilterSchema):
    prefix: str = ""


def to_stats(filters: StatsFilters) -> OrderStats:
    return OrderStats(prefix=filters.prefix)


class OrderController(Controller):
    place = use_case(post("/", response={201: OrderOut}), PlaceOrderHandler, command=to_place_order, status=201)
    stats = use_query(get("/stats", response=dict[str, int]), OrderStatsHandler, query=to_stats)
```

A GET payload becomes query parameters (`Annotated[Schema, Query()]`), so `?prefix=x`
reaches the handler. Handlers are resolved per request, so their constructor dependencies
(`Repository`, `RequestContext`, ...) are injected, and an `async def __call__` makes the
operation async.

## Domain events

A `DomainEvent` records something that happened. Publish it from a use case; handlers run
**after the transaction commits**, through a `TaskQueue`, so they never observe rolled-back
state.

```python
from dataclasses import dataclass

from ninja_devx import EventBus
from ninja_devx.cqrs import DomainEvent
from ninja_devx.layers import OnCommitTaskQueue


@dataclass(frozen=True, slots=True)
class OrderPlaced(DomainEvent):
    order_id: int


# Wiring (once, at startup)
container.singleton(EventBus, EventBus(OnCommitTaskQueue()))
container.resolve(EventBus).subscribe(OrderPlaced, notify_followers)


# In the handler, after the order is saved
class PlaceOrderHandler:
    def __init__(self, orders: Repository[Order], events: EventBus) -> None:
        self.orders, self.events = orders, events

    def __call__(self, command: PlaceOrder) -> Order:
        with self.orders.transaction():
            order = self.orders.add({...})
        self.events.publish(OrderPlaced(order_id=order.pk))
        return order
```

Use `RecordingTaskQueue` in tests and assert with `capture_commits`/`captured_commits`. For
durable, cross-process delivery, run handlers that enqueue `django.tasks` work, or publish
to the [transactional outbox](webhooks.md) instead.

## Unit of work

`ModelService` already wraps each write in `repository.transaction()`. Use `UnitOfWork`
when a use case combines several repositories or ORM operations that must commit together:

```python
from ninja_devx.cqrs import UnitOfWork

with UnitOfWork():
    stock.change(product, {"stock": product.stock - command.quantity})
    orders.add({"owner": user, "product": product, "quantity": command.quantity})
```

Pass `using="replica"` for another database and `durable=True` when it must be the
outermost atomic block. `UnitOfWork` is synchronous, like `transaction.atomic`; in an
async handler run the block through `sync_to_async`.

## What this is not

Deliberately absent, to match the project's [design principles](../project/scope.md):
a command/query bus, a global handler registry, automatic dispatch by name, and an
`Aggregate` base class. Django models are the aggregates; services and use cases are the
domain logic. See the [recipes](recipes.md) for a complete, tested layering.
