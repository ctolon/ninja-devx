# SaaS example: a multi-tenant issue tracker

Workspaces (tenants) with admin and member roles, projects and issues. The example demonstrates the following resource policies:

| Feature | Where |
|---|---|
| Tenant from `X-Workspace`, members only (`NINJA_DEVX["TENANT_RESOLVER"]`) | `tracker/tenancy.py` |
| Every query scoped to the workspace, tenant set on create | `tenant_field` in `tracker/api.py` |
| Issues nested under projects of the same workspace | `Parent(Project, tenant_field="workspace")` |
| Archive instead of delete, remember who archived, admin-only unarchive | `SoftDelete("archived_at", deleted_by=...)`, `routes` |
| `ETag` + 304, and `If-Match` required for issue writes (412/428) | `etag = ETag(field="updated_at", require_if_match=True)` |
| Cursor pagination following `?ordering=` | `pagination_class = CursorPagination` |
| Per-client and per-scope rate limits | `ClientRateThrottle`, `ScopedRateThrottle("issue-writes")` |
| `internal_notes` visible to workspace admins only | `VisibleTo(IsWorkspaceAdmin(), hidden="omit")` |
| Business rule as a domain error (409) in a service | `IssueService`, `IssueClosed(Conflict)` |
| RFC 9457 `application/problem+json` errors | `ERROR_FORMAT` |
| Private documents shared per member (object permissions, lists filtered in SQL, members only) | `DocumentController`: `ObjectPermissions`, `ObjectSharingMixin`, `validate_holder` |
| Audit log of issue changes with diffs, request id and workspace; `GET .../{pk}/history` | `AuditMixin`, `AuditHistoryMixin`, `audit_metadata` |
| CSV / JSON Lines export of the filtered issue list | `ExportMixin` |
| Request ids, `Server-Timing`, `/health/live` and `/health/ready` | `use_middleware`, `HealthController` in `config/urls.py` |
| Schemathesis contract tests over every operation | `tracker/tests/test_contract.py` |

```bash
uv run --project ../.. pytest -q
uv run --project ../.. mypy config tracker
```

The bearer token is the username (`DemoTokenAuth`). That is for the example only; use real
tokens.

## Run from a checkout

From the repository root, run `uv sync --locked --group docs --group verification`, then
change to this example's directory before running its commands. The example uses its own
local `db.sqlite3`; `migrate` persists tables across management commands. Tests use Django's
separate test database. Do not point these settings at application data.

```bash
cd examples/saas  # from the repository root
uv run --no-sync --project ../.. python manage.py migrate
uv run --no-sync --project ../.. pytest -q
```

`DemoTokenAuth` treats a username as a bearer token. Use it only with local test data.
Create a workspace and membership before using the API:

```bash
uv run --no-sync --project ../.. python manage.py shell -c 'from django.contrib.auth.models import User; from tracker.models import Workspace, Membership; u, _ = User.objects.get_or_create(username="alice"); w, _ = Workspace.objects.get_or_create(slug="acme", defaults={"name": "Acme"}); Membership.objects.get_or_create(user=u, workspace=w, defaults={"role": Membership.Role.ADMIN})'
uv run --no-sync --project ../.. python manage.py runserver 127.0.0.1:8000
```

Send `Authorization: Bearer alice` and `X-Workspace: acme`. The resolver checks membership;
the header alone is not authorization. The schema is at `/api/docs` and project endpoints
start at `/api/v1/projects/`. Issue updates require the current ETag in `If-Match`.
History responses use `{items, count}` and require the workspace administrator role.

## Deployment boundary

These settings use a public example secret, `DEBUG=True` and permissive hosts. Replace
secrets, host policy, authentication, TLS/proxy settings, persistent database configuration
and operational monitoring before deployment. Run `manage.py check --deploy` against the
actual production settings. Example payment, token and storage implementations are not
production services. See the root security policy and release runbook.
