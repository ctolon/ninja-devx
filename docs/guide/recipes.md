# Architecture recipes

ninja-devx supports layered architectures without imposing one. The
[`examples/recipes`](https://github.com/ctolon/ninja-devx/tree/main/examples/recipes)
project implements the same order API (place, list, cancel, with stock) three ways, over
the same models. One set of HTTP contract tests runs against all three.

| Recipe | Writes | Reads | Wiring |
|---|---|---|---|
| HackSoft | service functions (`order_place(*, user, ...)`) | selector functions | none |
| Cosmic-lite | use case handlers over `Repository[...]` ports, `RequestContext` | queryset in the controller | `Container` |
| Interactors | interactors over a gateway protocol | an interactor in `__init__` | dishka |

## HackSoft: services and selectors

```python
class OrderController(Controller):
    options = ControllerOptions(permissions=[IsAuthenticated()])

    @post("/", response={201: OrderOut})
    def place(self, request: HttpRequest, payload: OrderIn) -> Status[Order]:
        user = current_user(request, User)
        return Status(
            201, order_place(user=user, product_id=payload.product_id, quantity=payload.quantity)
        )
```

Services raise `OutOfStock(Conflict)`, and the controller never catches it
([Errors](errors.md)).

## Cosmic-lite: use cases and repositories

```python
class PlaceOrderHandler:
    def __init__(
        self, products: Repository[Product], orders: Repository[Order], context: Context
    ) -> None: ...

    def __call__(self, command: PlaceOrder) -> Order: ...


class OrderController(Controller):
    place = use_case(
        post("/", response={201: OrderOut}), PlaceOrderHandler, command=to_place_order, status=201
    )

    @post("/{pk}/cancel", response=OrderOut)
    def cancel(self, request: HttpRequest, pk: int, handler: Inject[CancelOrderHandler]) -> Order:
        return handler(CancelOrder(order_id=pk))
```

Handlers are unit-tested with `InMemoryRepository` and `make_context`, without a
database.

## Interactors with dishka

```python
provider = Provider(scope=Scope.REQUEST)
provider.from_context(provides=HttpRequest, scope=Scope.REQUEST)
provider.provide(acting_user)
provider.provide(DjangoOrderGateway, provides=OrderGateway, scope=Scope.APP)
provider.provide_all(PlaceOrderInteractor, CancelOrderInteractor, ListOrdersInteractor)
provide_controllers(provider, [OrderController])

mount(api, {"/orders": OrderController}, container=DishkaResolver(make_container(provider)))
```

`DishkaResolver` checks at startup that dishka provides the controller and every
`Inject[...]` dependency.

## Which one?

Start with plain controllers and CRUD. Move logic into services when a second caller
appears (a task, a command, another endpoint). Add ports and use cases when you need to
test business rules without the database, or to swap implementations. These are
per-feature decisions, and the recipes show they can live side by side in one API.
