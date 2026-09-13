# Audit log

!!! tip "Reference"

    [configuration reference](../options/contrib.md#audit-log-ninja_devxcontribaudit)

Answer "who changed this, when, and what was it before?" without writing the same
bookkeeping in every endpoint.

```python
INSTALLED_APPS += ["ninja_devx.contrib.audit"]  # then: manage.py migrate
```

```python
from ninja_devx.contrib.audit.api import AuditHistoryMixin, AuditLogController
from ninja_devx.contrib.audit.log import AuditMixin


class InvoiceController(
    AuditHistoryMixin[Invoice], AuditMixin[Invoice], CRUDController[Invoice, InvoiceOut, InvoiceIn]
):
    audit_exclude = ("password", "search_vector")
    audit_redact = ("iban",)

    def audit_metadata(self, request: HttpRequest) -> Mapping[str, object] | None:
        return {"tenant": request.auth.organization_id}


mount(api, {"/invoices": InvoiceController, "/audit": AuditLogController})
use_middleware(api, RequestIDMiddleware())  # entries keep the request id
```

## What is recorded

Every create, update and delete through the controller writes an `AuditEntry`. That
includes bulk operations and your own `perform_*` overrides.

| Field | Example |
|---|---|
| `action` | `create`, `update`, `delete`, or your own (`export`, `approve`) |
| `actor`, `actor_label` | the user, and its string form (kept if the user is deleted) |
| `content_type`, `object_pk`, `object_repr` | what was changed |
| `changes` | `{"status": ["draft", "sent"], "total": ["10.00", "12.50"]}` |
| `request_id`, `method`, `path`, `ip_address` | where it came from |
| `metadata` | `audit_metadata(request)` |

- Updates store only the fields that changed. Creates store `[null, value]` and deletes
  store `[value, null]`.
- The entry is written **in the same transaction** as the change. A failed write leaves
  no entry, and a committed write always has one.
- `audit_redact` fields show that they changed, but not the values (`["***", "***"]`).
- Foreign keys are stored as their key, and values are JSON (dates as ISO strings,
  decimals as strings).

List `AuditMixin` before the CRUD base class. It wraps `perform_create`,
`perform_update` and `perform_destroy` with `super()`. Startup checks such as input
schema validation still see your controller's real hooks.

## Recording other actions

```python
from ninja_devx.contrib.audit.log import record

record(request, "export", invoice, metadata={"format": "pdf"})
record(None, "expire", subscription)  # from a job: no actor or request data
```

## Reading the log

- `AuditLogController` (staff only by default) provides `GET /`, filterable by `action`,
  `actor_id`, `model` (`app_label.model`), `object_pk`, `request_id`, `since` and `until`,
  ordered by `created`. `GET /{pk}` returns one entry.
- `AuditHistoryMixin` adds `GET /{pk}/history` to a controller. The object is loaded like
  `retrieve`, preserving tenant, owner, object permissions and soft-delete scope. Staff
  permission is additionally required by default. Override the history operation permissions
  through `routes` to implement a dedicated audit-reader role.

Change the log's permissions like any controller:

```python
class Audit(AuditLogController):
    options = ControllerOptions(
        permissions=[HasDjangoPermission("ninja_devx_audit.view_auditentry")]
    )
```

## Admin

Entries appear read-only in the Django admin, with filters for action and model, a date
drill-down, and search by object, actor or request id. Only superusers can delete them.

## Retention

Entries are ordinary rows. Delete old ones on a schedule:

```python
AuditEntry.objects.filter(created__lt=now() - timedelta(days=365)).delete()
```

## Pagination and storage privacy

The log and object history return `{"items": [...], "count": N}`. Both default to 50
items, cap `page_size` at 100, and accept `page`; history is no longer silently truncated
at 500 records. Ordering includes the primary key to make timestamp ties deterministic.

`AuditPrivacy` filters data before it reaches the database. Passwords, hashed secrets and
private keys are excluded by default; credential token fields are redacted. Credential
keys in nested metadata are redacted case-insensitively. Model `__str__` is not recorded
by default: the object label contains its model name and key.

```python
from ninja_devx.contrib.audit.privacy import AuditPrivacy

class PrivateInvoices(AuditMixin[Invoice], CRUDController[Invoice, InvoiceOut, InvoiceIn]):
    audit_privacy = AuditPrivacy(
        fields=("status", "total", "iban"),
        redact=("iban", "token", "access_token", "refresh_token", "secret", "api_key"),
        metadata_fields=("tenant", "source"),
    )
```

Use a field allowlist for application-specific confidential data. Output-schema visibility
rules do not erase stored audit values; configure the storage policy and audit-reader
permissions independently. `record(..., privacy=...)` accepts the same policy.

Audit entries and their ContentType use the object's database alias. Bulk, repository and
audit transactions follow the write router. Custom persistence hooks must write on that
same alias for all-or-nothing behavior; cross-database writes cannot share one transaction.
