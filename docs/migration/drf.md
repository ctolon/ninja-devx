# Migrating from Django REST framework

Treat the migration as an HTTP contract change, even when both implementations use the
same Django model. Serializers, authentication, pagination and exception handlers can
change what clients observe. Port one resource behind a separate URL prefix, compare its
behavior, and switch traffic only after the differences are intentional.

DRF's [ViewSets](https://www.django-rest-framework.org/api-guide/viewsets/) group resource
actions; its [serializers](https://www.django-rest-framework.org/api-guide/serializers/)
combine representation, validation and persistence hooks. ninja-devx uses separate Ninja
schemas and model/service hooks. Existing Django models and database migrations can remain.

## 1. Record the existing contract

Capture examples for list/detail/create/PUT/PATCH/delete and custom actions. Include:

- Exact URL, trailing slash, route name and HTTP method.
- Anonymous, authenticated, wrong-owner, wrong-tenant and staff requests.
- Empty and malformed bodies, unknown fields, omitted fields, explicit null and duplicates.
- Pagination envelope, default ordering, maximum page size and filter syntax.
- Decimal/date/time/UUID representation, related IDs and nested objects.
- Status, error body, relevant headers, cookies and content type.
- Side effects: signals, audit records, task dispatch, remote calls and transactions.

Keep these as application tests or fixtures. An OpenAPI diff is useful but does not capture
all authorization or side-effect behavior. Never use a mutating production request as a
shadow request against both implementations unless side effects are isolated.

## 2. Separate input and output

For a model with `owner`, `title`, `body` and `done`, an owner-scoped DRF view often looks like:

```python
from rest_framework import permissions, serializers, viewsets

from notes.models import Note


class NoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Note
        fields = ("id", "title", "body", "done")
        read_only_fields = ("id", "done")


class NoteViewSet(viewsets.ModelViewSet):
    serializer_class = NoteSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Note.objects.filter(owner=self.request.user)

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)
```

The equivalent resource configuration in ninja-devx is explicit about schemas:

```python
from typing import Annotated

from ninja import Schema
from ninja_devx.crud import CRUDController
from pydantic import Field

from notes.models import Note


class NoteIn(Schema):
    title: Annotated[str, Field(min_length=1, max_length=200)]
    body: str = ""


class NoteOut(Schema):
    id: int
    title: str
    body: str
    done: bool


class NoteController(CRUDController[Note, NoteOut, NoteIn]):
    owner_field = "owner"
    scope_queryset_to_owner = True
```

`owner` is not an input field. The controller sets it from the authenticated request.
The [Quickstart](../getting-started/quickstart.md) is a complete tested project with this
pattern, a custom action and pagination. Do not expose all model fields merely to make a
serializer conversion shorter.

Translate `validate_<field>` into a Pydantic field validator and cross-field input rules
into a model validator. Rules that require the current row or transaction belong in a
service or `perform_*` hook. Default model persistence calls `full_clean`; this may reject
values the old endpoint accepted. Database uniqueness remains a constraint even when a
pre-save validator passes.

`PATCH` uses only submitted fields. Explicit `null` is still a submitted value and is not
the same as omission. PUT applies the input schema, including defaults. Test nested write
semantics separately; a nested output schema does not implement nested object creation.

## 3. Mount alongside the old router

```python
from django.urls import include, path
from ninja import NinjaAPI
from ninja.security import django_auth

from notes.api import NoteController
from notes.legacy_urls import router as legacy_router

api = NinjaAPI(title="Notes migration", urls_namespace="notes-v2", auth=django_auth)
api.add_router("/notes", NoteController.as_router())

urlpatterns = [
    path("api/v1/", include(legacy_router.urls)),
    path("api/v2/", api.urls),
]
```

`django_auth` is an example for a session-based application. It is not an adapter for an
existing DRF token/JWT class. Implement a Ninja auth callable for that credential format,
or use a maintained integration, and verify CSRF, cookie and invalid-token behavior.
DRF authentication classes may return a `(user, auth)` pair; do not paste that method into
a Ninja auth class and assume the request user will be set correctly. Consult the
[DRF authentication contract](https://www.django-rest-framework.org/api-guide/authentication/)
and [Ninja authentication](https://django-ninja.dev/guides/authentication/).

## 4. Move hooks by responsibility

| DRF surface | Destination | Migration check |
|---|---|---|
| `get_queryset()` | `get_queryset(request)` | Preserve tenant/owner filters, annotations and relation loading |
| `serializer.save(owner=...)` | `owner_field` or `context_data(request)` | Context-controlled fields stay out of the input schema |
| `perform_create(serializer)` | `perform_create(request, payload)` | Return a saved model; preserve selected DB and transaction |
| `perform_update(serializer)` | `perform_update(request, instance, data)` | `data` contains intended changes; preserve partial-update semantics |
| `perform_destroy(instance)` | `perform_destroy(request, instance)` | Decide hard vs soft deletion and external cleanup timing |
| `@action(detail=True)` | Explicit route plus Instance/Locked binding | Preserve method/path/permissions; call policy checks in custom lookups |
| `self.request` / `self.action` | Request argument / `get_operation(request)` | Avoid storing request state on singleton controllers |
| Exception handler | ErrorMap or explicit Ninja handler | Match status/body if clients rely on old DRF errors |
| Serializer method field | Ninja resolver | Provide queryset hints for relations accessed by Python code |
| Filter backends | FilterSchema or controller filter/search/order settings | Parameter names and invalid-order behavior can differ |
| Authentication/permission classes | Ninja auth and new permission objects | They are separate interfaces, not drop-in imports |

For a custom completion action:

```python
from django.http import HttpRequest
from ninja_devx import post
from ninja_devx.crud import Instance

# Add to NoteController. Note and NoteOut are the model/schema above.
@post("/{pk}/complete", response=NoteOut)
def complete(self, request: HttpRequest, note: Instance[Note]) -> Note:
    note.done = True
    note.save(update_fields=["done"])
    return note
```

This custom method does not automatically acquire the transaction/ETag policy of a built-in
update. Use a transactional service or the documented `Locked`/atomic patterns when that
operation needs concurrency protection.

## 5. Rebuild authorization deliberately

DRF's [object-permission guidance](https://www.django-rest-framework.org/api-guide/permissions/)
distinguishes detail checks from list filtering. Keep that distinction after migration.
`IsOwner` checks an object; `scope_queryset_to_owner=True` restricts query results. Set a
tenant field only with a resolver that verifies tenant membership. Use `Parent` for nested
resources rather than trusting a parent ID from the URL.

Built-in writes verify the persisted result against scope and object permissions before
commit. A custom hook that returns an out-of-scope row raises 404 and rolls back on the
selected alias. Applications using object grants must establish required permissions for
the newly created object inside that transaction. See [persistence boundaries](../guide/crud.md#persistence-and-database-boundaries).

Permission lists on a route replace inherited configuration where documented; use
`Also(...)` when extending it. Test negation/composition with both request and object
checks. Do not translate permission class names mechanically.

## 6. Preserve or version transport differences

| Existing client assumption | Default/new behavior to inspect |
|---|---|
| Invalid input is 400 | Ninja schema/model validation commonly returns 422 |
| Page response has `results/next/previous/count` | Ninja PageNumber responses use `items/count`; choose/adapt a paginator |
| Unknown ordering is ignored | Generated ordering fields reject unsupported values |
| Omitted and null fields are interchangeable | PatchData distinguishes submission from omission |
| Session auth always returns the same 401/403 response | Chosen auth and CSRF flow determine rejection behavior |
| HTML browsable API or alternate renderer is available | Ninja docs UI and configured renderer are different interfaces |
| Serializer output implicitly loads relations | Queryset loading must satisfy schemas and arbitrary resolvers |
| API key can create another credential | Built-in key management rejects API-key-authenticated requests |

For pagination customization consult [DRF pagination](https://www.django-rest-framework.org/api-guide/pagination/)
and the [CRUD pagination guide](../guide/crud.md). If existing clients cannot change,
implement and test a compatibility envelope or retain the old endpoint through a deprecation
window. Do not silently relabel a 422 body as a DRF serializer error.

## 7. Cut over with a rollback path

Run the same accepted-request fixtures against both prefixes and document intentional
response differences. Add cross-tenant and wrong-owner tests; compare database state after
failed writes and confirm tasks publish only after commit. Measure query count for nested
lists and exports. Test on the production database engine, not only SQLite.

Switch one consumer or resource at a time. Keep database changes compatible with both
implementations until rollback is no longer needed. Regenerate clients only after operation
IDs and response schemas stabilize. Remove the old route and dependencies after its callers
have migrated; retaining an obsolete permission class as an unused import is not a completed
migration.
