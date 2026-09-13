# Quickstart example

The smallest useful ninja-devx project: a private notes API with search, filters,
ordering, pagination, ownership and a custom action. It is the code of the
[Quickstart](../../docs/getting-started/quickstart.md).

```bash
uv run --project ../.. python manage.py migrate
uv run --project ../.. python manage.py createsuperuser
uv run --project ../.. python manage.py runserver   # http://127.0.0.1:8000/api/docs
uv run --project ../.. pytest -q
```

## Run from a checkout

From the repository root, run `uv sync --locked --group docs --group verification`, then
change to this example's directory before running its commands. The example uses its own
local `db.sqlite3`; `migrate` persists tables across management commands. Tests use Django's
separate test database. Do not point these settings at application data.

```bash
cd examples/quickstart  # from the repository root
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

## Deployment boundary

These settings use a public example secret, `DEBUG=True` and permissive hosts. Replace
secrets, host policy, authentication, TLS/proxy settings, persistent database configuration
and operational monitoring before deployment. Run `manage.py check --deploy` against the
actual production settings. Example payment, token and storage implementations are not
production services. See the root security policy and release runbook.
