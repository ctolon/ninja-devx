# Async API example: orders with payments and a live feed

An async-first API where the cost of async Django (thread hops) stays visible and small:

| Feature | Where |
|---|---|
| Every CRUD operation registered async (`NINJA_DEVX["ASYNC_MODE"] = "async"`) | `config/settings.py` |
| One thread hop per write (plus one for Ninja's session auth), asserted with `assert_max_hops` | `orders/tests/test_orders.py` |
| An async, request-scoped dependency with cleanup (`async def` generator factory) | `orders/payments.py`, `config/urls.py` |
| `Inject[PaymentGateway]` in one operation, overridden in tests | `OrderController.pay` |
| Awaited network call, then `run_atomic` with `select_for_update` in one hop | `OrderController.pay` |
| Server-sent events with async ORM iteration | `OrderController.events` |
| An async operation hook timing whole streams | `Timing` |
| Nested schema joined automatically, lazy loads forbidden (`ASYNC_FETCH_MODE`) | `OrderOut.product` |
| Domain errors for 402 and 409, documented with `raises=` | `PaymentDeclined`, `AlreadyPaid` |

```bash
uv run --project ../.. pytest -q
uv run --project ../.. --with uvicorn uvicorn config.asgi:application
```

## Run from a checkout

From the repository root, run `uv sync --locked --group docs --group verification`, then
change to this example's directory before running its commands. The example uses its own
local `db.sqlite3`; `migrate` persists tables across management commands. Tests use Django's
separate test database. Do not point these settings at application data.

```bash
cd examples/async_api  # from the repository root
uv run --no-sync --project ../.. python manage.py migrate
uv run --no-sync --project ../.. pytest -q
```

For a browser session, create a local account and start the server:

```bash
uv run --no-sync --project ../.. python manage.py createsuperuser
uv run --no-sync --project ../.. python manage.py runserver 127.0.0.1:8000
```

Open `http://127.0.0.1:8000/accounts/login/`, sign in, then visit `/api/docs`. Login uses
Django's session and CSRF machinery; an API's permission check does not replace CSRF.
The automated Ninja tests inject a user and do not demonstrate a production login provider.

Use ASGI to exercise streaming in a running server:

```bash
uv run --project ../.. --with uvicorn uvicorn config.asgi:application --host 127.0.0.1 --port 8000
```

Create products through `manage.py shell` before creating orders; `orders/tests/test_orders.py`
shows the minimal fixture. The payment gateway is a fake. A real provider needs an
idempotency key and reconciliation because a remote charge and local commit are not one
transaction. The timing hook keeps a bounded in-memory sample on its own instance; use a
metrics backend for durable telemetry. SSE is served by the API but is not supported by
the generated clients.

## Deployment boundary

These settings use a public example secret, `DEBUG=True` and permissive hosts. Replace
secrets, host policy, authentication, TLS/proxy settings, persistent database configuration
and operational monitoring before deployment. Run `manage.py check --deploy` against the
actual production settings. Example payment, token and storage implementations are not
production services. See the root security policy and release runbook.
