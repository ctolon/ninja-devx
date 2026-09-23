# Nested writes

Write a parent and its child collections in one request instead of a create call per
child, and know what a `PUT`/`PATCH` actually changed without diffing it yourself.

## Declaring child collections

```python
from ninja_devx.crud.nested_writes import Nested, NestedWritesMixin


class OrderItemIn(Schema):
    id: int | None = None
    product: int
    quantity: int


class OrderIn(Schema):
    customer: int
    items: list[OrderItemIn] = []


class OrderController(NestedWritesMixin[Order, OrderIn], CRUDController[Order, OrderOut, OrderIn]):
    nested = {"items": Nested(OrderItem, "order", OrderItemIn)}
```

`Nested(OrderItem, "order", OrderItemIn)`: `OrderItem.order` is the foreign key back to
`Order`, and `OrderItemIn` validates each item. `OrderIn` must declare the matching field
(`items: list[OrderItemIn]`); a missing or misspelled field fails at startup, so the
OpenAPI schema and the write path never disagree.

List `NestedWritesMixin` before the CRUD base, like `SoftDeleteMixin`, so its
`perform_create`/`perform_update` win.

## Create

`POST /` saves the parent, then every item, all inside the operation's transaction: one
item failing validation rolls back the parent and every other item too. Errors are
reported at the item's position, in Ninja's error format:

```json
{"detail": [{"type": "validation_failed", "loc": ["body", "items", 1, "quantity"], "msg": "..."}]}
```

## Update

`PUT`/`PATCH /{pk}` sync each declared collection present in the payload against the
parent's existing children, matched by `key` (default `"id"`):

- an item sent with its key matching an existing child updates that child in place;
- an item sent without the key (or with one that matches nothing) creates a new child;
- an existing child whose key is absent from the payload is deleted, unless
  `Nested(..., remove_missing=False)`.

`PUT` always applies this (an omitted field falls back to its schema default, so leaving
`items` out of a `PUT` clears the children, matching full-replacement semantics). A
`PATCH` that does not send the field at all leaves the children untouched entirely.

Deletion is hard, not soft: children of a `SoftDeleteMixin` controller are out of scope
here and are deleted outright.

## Change tracking

`ModelController.on_change(request, instance, changes)` runs after a successful `PUT`,
`PATCH` or bulk update, inside the same transaction. `changes` is `{field: (old, new)}`
for the fields the request actually sent whose value differs; it is not called when
nothing changed. Foreign keys compare by primary key. The default is a no-op:

```python
class InvoiceController(CRUDController[Invoice, InvoiceOut, InvoiceIn]):
    def on_change(self, request, instance, changes):
        if "status" in changes:
            notify_status_change(instance, *changes["status"])
```

An exception raised from `on_change` rolls back the update along with it, since it runs
inside `write_scope`.

`ninja_devx.crud.persistence.changed_fields(instance, data)` is the helper behind the
hook: call it yourself from a service or a repository, before writing `data` to
`instance`.

## Integration

Both features are independent: use `NestedWritesMixin` without touching `on_change`, or
override `on_change` on a controller with no `nested` collections at all.
