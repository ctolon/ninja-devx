# Examples

Every example is a complete Django project with its own tests. CI runs them all
(`pytest`, `mypy --strict`, `makemigrations --check`, `manage.py check`,
`devx_scaffold --check`).

| Example | What it shows | Size |
|---|---|---|
| [`quickstart`](https://github.com/ctolon/ninja-devx/tree/main/examples/quickstart) | a private notes API: CRUD, ownership, search, filters, a custom action | 1 app |
| [`blog`](https://github.com/ctolon/ninja-devx/tree/main/examples/blog) | the [tutorial](tutorial.md): scaffolding, soft delete, nested comments, idempotency, versioned mount, generated TypeScript and Python clients | 1 app |
| [`recipes`](https://github.com/ctolon/ninja-devx/tree/main/examples/recipes) | one order API written three ways (HackSoft services, Cosmic-lite use cases and repositories, dishka interactors) passing the same contract tests | 4 apps |
| [`saas`](https://github.com/ctolon/ninja-devx/tree/main/examples/saas) | a multi-tenant issue tracker: `tenant_field`, roles, archiving, `ETag`/`If-Match`, cursor pagination, throttles, field visibility, problem+json, schemathesis | 1 app |
| [`async_api`](https://github.com/ctolon/ninja-devx/tree/main/examples/async_api) | async-first orders: one hop per write, async dependencies with cleanup, `run_atomic`, server-sent events, async hooks | 1 app |

## Run one

```bash
git clone https://github.com/ctolon/ninja-devx && cd ninja-devx
uv sync --locked --group docs --group verification
cd examples/saas
uv run --project ../.. pytest -q
uv run --project ../.. python manage.py migrate
uv run --project ../.. python manage.py runserver   # http://127.0.0.1:8000/api/docs
```

Session-based examples include `/accounts/login/` and a local login template. Run
`createsuperuser` and sign in before using their docs UI. The SaaS example uses a deliberately
insecure username bearer token; follow its README to create a workspace/member and send
both required headers. Each README names its deployment boundaries. These projects are
executable library demonstrations, not production templates.

## Which example for which question

| Question | Look at |
|---|---|
| How little code does a CRUD API need? | `quickstart/notes/api.py` |
| How do I isolate tenants and roles? | `saas/tracker/tenancy.py`, `saas/tracker/api.py` |
| Where does business logic go? | `recipes/` (three answers), `saas/tracker/api.py` (`IssueService`) |
| How do I keep async code fast? | `async_api/orders/api.py` and its hop assertions |
| How do I generate clients and keep them current? | `blog/clients/`, `blog/blog/tests/test_api.py` |
| How do I fuzz my API from its schema? | `saas/tracker/tests/test_contract.py` |
