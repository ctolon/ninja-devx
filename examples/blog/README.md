# Blog example

A small API built with ninja-devx: owner-scoped posts with search, filters,
soft delete, an idempotent publish action, nested comments and read-only tags,
mounted under `/v1`, plus generated TypeScript and Python clients.

```bash
cd examples/blog
uv run --project ../.. python manage.py migrate
uv run --project ../.. python manage.py runserver     # docs at /api/docs
uv run --project ../.. pytest
```

How it was built:

```bash
python manage.py devx_scaffold blog.Post --owner author --output blog/api/posts.py
python manage.py devx_scaffold blog.Comment --write body --output blog/api/comments.py --no-tests
# ...then blog/api/*.py were edited by hand (see the module docstrings)
python manage.py devx_openapi config.urls.api --format typescript --output clients/blog.ts
python manage.py devx_openapi config.urls.api --format python --output clients/blog_client.py
```

## Run from a checkout

From the repository root, run `uv sync --locked --group docs --group verification`, then
change to this example's directory before running its commands. The example uses its own
local `db.sqlite3`; `migrate` persists tables across management commands. Tests use Django's
separate test database. Do not point these settings at application data.

```bash
cd examples/blog  # from the repository root
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

Post creation assigns the session user as author. Publish requests use an `Idempotency-Key`;
reusing it with a different request is a conflict. The example's idempotency tests use
transaction-enabled fixtures so the durable claim can commit. Nested comment endpoints
validate the parent post. Generated clients are checked by the example test suite; rerun
`devx_openapi` after changing schemas or explicit operation IDs.

## Deployment boundary

These settings use a public example secret, `DEBUG=True` and permissive hosts. Replace
secrets, host policy, authentication, TLS/proxy settings, persistent database configuration
and operational monitoring before deployment. Run `manage.py check --deploy` against the
actual production settings. Example payment, token and storage implementations are not
production services. See the root security policy and release runbook.
