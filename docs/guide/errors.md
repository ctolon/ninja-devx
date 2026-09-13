# Errors

!!! tip "Reference"

    Every option on this page, with types and defaults: [configuration reference](../options/errors.md).

Business code raises exceptions and knows nothing about HTTP. The mapping to responses
lives in one place per layer, and uses Ninja's own exception handling at the API level.

## Domain errors

```python
from ninja_devx.layers import Conflict, DomainError, NotFound


class OutOfStock(Conflict):  # 409
    code = "out_of_stock"


class PaymentRequired(DomainError):
    http_status = 402
    code = "payment_required"


raise OutOfStock("Only 3 left", available=3)
# 409 {"detail": "Only 3 left", "code": "out_of_stock", "available": 3}
```

`ninja_devx.layers` never imports Ninja, so services and repositories can raise these from
tasks and management commands too. The built-ins are `NotFound` (404), `Conflict` (409),
`PermissionDenied` (403), `ValidationFailed` (422) and `PolicyDenied` (403).

Any exception class with integer `http_status` and string `code` attributes maps itself.
It doesn't need to subclass `DomainError`: this is the `HttpMappable` protocol.

## Error maps

For exceptions you don't own, declare rules:

```python
from ninja_devx import ControllerOptions, ErrorMap

errors = (
    ErrorMap()
    .map(StripeCardError, 402, code="card_declined")
    .map(RateLimited, 429, body=lambda exc: {"detail": str(exc), "retry_after": exc.seconds})
)


class OrderController(Controller):
    options = ControllerOptions(errors=errors)
```

`body` is typed against the exception class, so `exc.seconds` type-checks.

Rules apply in layers. The closest layer wins, and within a layer the closest class in the
exception's MRO wins:

1. operation `errors=`
2. controller `options.errors` (and `as_router(errors=...)`)
3. `NINJA_DEVX["ERRORS"]`: an `ErrorMap` or its import path
4. the defaults: Django's `ValidationError` → 422, `ObjectDoesNotExist` → 404,
   `PermissionDenied` → 403
5. whatever Ninja's `api.add_exception_handler` handles

Controller and operation rules apply inside the view. A router tested on its own
(`TestClient(Controller.as_router())`) behaves exactly like the mounted one.

To register rules with Ninja for every view of an API, including function views, use
`errors.install(api)`. `mount(api, routes, errors=errors)` does it for you.

Unmapped exceptions propagate unchanged. Nothing is swallowed.

## Documenting errors

```python
@post("/", response={201: OrderOut}, raises=(OutOfStock, PaymentRequired))
def place(self, request: HttpRequest, payload: OrderIn) -> Status[Order]: ...
```

`raises` adds the statuses to OpenAPI with a `{detail, code}` schema. `as_router()`
fails when a listed exception has no rule, so the documentation cannot drift from
behavior. 401/403/404/422 are documented automatically (`document_errors`).

## Format

`NINJA_DEVX["ERROR_FORMAT"] = "problem+json"` renders
[RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) bodies with
`application/problem+json`:

```json
{"type": "about:blank", "title": "Conflict", "status": 409, "code": "out_of_stock",
 "detail": "Only 3 left", "available": 3}
```

The default `"ninja"` format matches Ninja's `{"detail": ...}` responses.
