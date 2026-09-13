# Architecture recipes

One order API (place, list, cancel) implemented three ways over the same models
(`shop`), and verified by the **same** HTTP contract tests (`tests/test_contract.py`).
None of the styles needs anything from `ninja_devx` that the others don't: pick the
one that fits the team, or mix them per feature.

| Recipe | Writes | Reads | Wiring | Unit tests without a DB |
|---|---|---|---|---|
| [`hacksoft`](hacksoft) | service functions | selector functions | none | – |
| [`cosmic`](cosmic) | use case handlers (`use_case(...)`, `Inject[...]`) over `Repository[...]` ports | queryset in the controller | `ninja_devx.Container` + `RequestContext` | `InMemoryRepository`, `make_context` |
| [`interactors`](interactors) | interactors over a gateway protocol | interactor injected in `__init__` | dishka + `DishkaResolver`, `provide_controllers` | swap the gateway |

What stays the same everywhere:

- Business errors are `DomainError` subclasses (`shop/errors.py`); `ninja_devx` maps
  them to responses (`409 {"detail", "code", ...}`) without an exception handler.
- Permissions are declared on the controller (`IsAuthenticated()`).
- `manage.py check` and `manage.py devx_scaffold --check` run in the tests.

```bash
uv run --project ../.. pytest -q
uv run --project ../.. mypy config shop hacksoft cosmic interactors
```

## Run from a checkout

From the repository root, run `uv sync --locked --group docs --group verification`, then
change to this example's directory before running its commands. The example uses its own
local `db.sqlite3`; `migrate` persists tables across management commands. Tests use Django's
separate test database. Do not point these settings at application data.

```bash
cd examples/recipes  # from the repository root
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

The three prefixes are `/api/hacksoft/orders`, `/api/cosmic/orders` and
`/api/dishka/orders`. They share models, domain errors and a contract test. Read the
service/handler tests before adding another interface: the point is to compare ownership
of business rules and dependencies, not to require every style in one application.

## Deployment boundary

These settings use a public example secret, `DEBUG=True` and permissive hosts. Replace
secrets, host policy, authentication, TLS/proxy settings, persistent database configuration
and operational monitoring before deployment. Run `manage.py check --deploy` against the
actual production settings. Example payment, token and storage implementations are not
production services. See the root security policy and release runbook.
