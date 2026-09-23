# Version changes

Public API changes by release, with the edits an upgrading application needs. Migration
guides from other frameworks are separate pages.

## 0.0.2

- `GrantsBackend` moved from `ninja_devx.security.object_permissions` to
  `ninja_devx.contrib.grants.backends`. Update the import; the object-permission registry
  resolves the built-in backend by name (`"grants"`) once `ninja_devx.contrib.grants` is
  installed.
- A controller with a `search_backend` keeps its `search` query parameter in the generated
  filter schema and in OpenAPI; the backend applies the term instead of the generated
  `icontains` lookups. Generated clients need no change.
- `rotate_api_key` and `devx_apikey rotate` refuse a revoked key instead of reviving it;
  create a new key instead. Pass `rate_limit=None` to clear the limit while rotating.
- `devx_openapi --against` combined with `--output` writes the current document after the
  comparison passes, so one command refreshes a baseline.
- `LimitOffsetPagination` and `CursorPagination` both publish link metadata for
  `PaginationHeadersMiddleware`; `X-Total-Count` is set only when a count exists.
- `ninja_devx.testing.sample`/`samples` return JSON-serialisable values (UUIDs and
  timestamps as strings) and raise `TypeError` for a field type they cannot fill.

## 0.0.1

These changes shipped in the **0.0.1** release. They intentionally changed APIs before the
first release. No CI or Git configuration was changed.

### Module layout

Root symbol imports such as `from ninja_devx import Controller, Container, get` remain
available. Direct module imports use the following locations. Private module names
remain implementation details.

| Previous module | Current module |
|---|---|
| `ninja_devx._compat` | `ninja_devx._internal.compat` |
| `ninja_devx._generics` | `ninja_devx._internal.generics` |
| `ninja_devx._i18n` | `ninja_devx._internal.i18n` |
| `ninja_devx._types` | `ninja_devx._internal.types` |
| `ninja_devx._pydantic` | `ninja_devx.serialization.pydantic` |
| `ninja_devx._instances` | `ninja_devx.dependencies.instances` |
| `ninja_devx._permission_leaf` | `ninja_devx.security.permission_leaf` |
| `ninja_devx._registration` | `ninja_devx.routing.compiler` |
| `ninja_devx.auth` | `ninja_devx.security.auth` |
| `ninja_devx.permissions` | `ninja_devx.security.permissions` |
| `ninja_devx.object_permissions` | `ninja_devx.security.object_permissions` |
| `ninja_devx.tenancy` | `ninja_devx.security.tenancy` |
| `ninja_devx.di` | `ninja_devx.dependencies.container` |
| `ninja_devx.inject` | `ninja_devx.dependencies.injection` |
| `ninja_devx.bindings` | `ninja_devx.routing.bindings` |
| `ninja_devx.controller` | `ninja_devx.routing.controller` |
| `ninja_devx.operations` | `ninja_devx.routing.operations` |
| `ninja_devx.mounting` | `ninja_devx.routing.mounting` |
| `ninja_devx.plugins` | `ninja_devx.routing.plugins` |
| `ninja_devx.hooks` | `ninja_devx.routing.hooks` |
| `ninja_devx.use_cases` | `ninja_devx.routing.use_cases` |
| `ninja_devx.conditional` | `ninja_devx.http.conditional` |
| `ninja_devx.middleware` | `ninja_devx.http.middleware` |
| `ninja_devx.throttling` | `ninja_devx.http.throttling` |
| `ninja_devx.health` | `ninja_devx.http.health` |
| `ninja_devx.errors` | `ninja_devx.http.errors` |
| `ninja_devx.schemas` | `ninja_devx.serialization.schemas` |
| `ninja_devx.visibility` | `ninja_devx.serialization.visibility` |
| `ninja_devx.renderers` | `ninja_devx.serialization.renderers` |
| `ninja_devx.conf` | `ninja_devx.configuration.settings` |
| `ninja_devx.checks` | `ninja_devx.configuration.checks` |
| `ninja_devx.scaffold` | `ninja_devx.tooling.scaffold` |
| `ninja_devx.drift` | `ninja_devx.tooling.drift` |
| `ninja_devx.unasync` | `ninja_devx.tooling.unasync` |
| `ninja_devx.testing` | `ninja_devx.testing.clients` |
| `ninja_devx.pytest_plugin` | `ninja_devx.testing.plugin` |
| `ninja_devx.contract` | `ninja_devx.testing.contracts` |

Routing compilation lives in `routing.compiler`; request execution and cleanup live in
`routing.invocation`. Dependency contracts, registration, resolution and scope cleanup
live in `dependencies.contracts`, `container`, `engine` and `scope`, respectively.
The root `unasync` and pytest plugin modules retain installed command entry points.

Router metadata is owned by each router; enumeration and settings caches belong to the
Django application registry. Generated schema and generic-resolution caches belong to
the source class. Subclasses do not inherit cached resolutions from their parents.

### Behavior changes

- Run Django migrations with `ninja_devx` installed before enabling idempotency.
  Replace `IDEMPOTENCY_CACHE` with `IDEMPOTENCY_DATABASE`. The claim connection must
  use autocommit; use a separate database alias when the request has an outer transaction.
  Claims for cancelled or indeterminate operations require investigation before removal.
  See [hooks and idempotency](../guide/hooks.md).
- API keys cannot manage credentials, even when their scope is `*`.
- Webhook publication requires an owner, a tenant key, or explicit `broadcast=True`.
  Queued webhook tasks now receive `(event_id, database_alias)` after commit.
- Conditional writes use strong ETags and a transaction on the write database.
  Weak validators cannot satisfy `If-Match`.
- List selectors must return a QuerySet of the controller model; mandatory scope is
  applied even when a selector supplies that queryset.
- Duplicate bulk IDs fail validation instead of silently changing request semantics.
- Generated clients expose per-call headers and response callbacks. Unsupported request
  formats and parameter encodings fail generation explicitly. Regenerate clients after
  changing the API; see [client generation](../guide/codegen.md).

- API key, audit log and audit history lists now return `items`/`count` pagination.
  History requires staff permission in addition to object access unless explicitly overridden.
- Audit storage excludes common credential fields, redacts nested credential metadata,
  and uses a model/key label instead of the model's `__str__` by default.

- Upload completion now requires a durable UploadRecord from signing; run the new core
  migration. Custom signers accept `checksum_sha256` and implement version-aware deletion.
  Completion responses add checksum/version/ETag metadata; store version IDs for immutable
  references. Expired pending uploads can be cleaned with `devx_uploads`.

### Persistence, history and examples

Create/update/bulk/import now verify the persisted result against the controller's
queryset and object permissions before committing. Returning an out-of-scope object
from a custom hook produces 404 and rollback on the selected write alias. A disabled
`refresh_after_write` does not disable these checks. Controllers using `ObjectPermissions`
must arrange the required post-create permissions as part of the create transaction;
the sharing example grants its author `add`, `view`, `change` and `delete`.

Audit history now returns `{items, count}` and requires staff by default. Applications
may explicitly replace that route's permission with their own audit-reader role; the
SaaS example uses workspace administrators. Model scope still applies.

Tests calling durable idempotent endpoints must use transaction-enabled database fixtures
so claims can commit independently. Django `TestCase`/ordinary pytest `django_db` wrappers
are outer transactions; use `TransactionTestCase` or `django_db(transaction=True)` for
these tests, or configure a dedicated idempotency database alias.
