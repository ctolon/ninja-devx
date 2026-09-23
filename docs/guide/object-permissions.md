# Object permissions

!!! tip "Reference"

    Every option on this page, with types and defaults:
    [configuration reference](../options/permissions.md#objectpermissions).

Model permissions say *what kind* of thing a user may do (`blog.change_post`). Object
permissions say *on which rows*: Bob may edit this document, and the Designers group may
view that folder. ninja-devx uses the same model as django-guardian and DRF's
`DjangoObjectPermissions`. Permissions are Django's `app_label.codename` strings, granted
to users or groups on single objects.

```python
from ninja_devx.crud import CRUDController
from ninja_devx.security.object_permissions import ObjectPermissions, assign_perm


class DocumentController(CRUDController[Document, DocumentOut, DocumentIn]):
    object_permissions = ObjectPermissions()


assign_perm("docs.view_document", bob, document)
assign_perm("docs.change_document", designers, document)  # a Group
```

With `object_permissions` set:

| Request | Needs on the object | Without it |
|---|---|---|
| `GET /` | `view` | the object is not listed |
| `GET /{pk}` | `view` | 404 |
| `PUT` / `PATCH /{pk}` | `change` | 403 (404 if the caller cannot view it either) |
| `DELETE /{pk}` | `delete` | 403 (404 if the caller cannot view it either) |
| `POST /` | — (no object yet) | use `DjangoModelPermissions` or a policy |

Callers who cannot see an object get 404, not 403, so its existence does not leak
(`hide_forbidden=False` changes that). Anonymous requests get 401.

Custom operations use the same check whenever they load the object through the controller
(`Instance[Model]`, `get_object()`), because `ObjectPermissions` is an ordinary
permission of the controller. You can also use it on any controller:

```python
@post(
    "/{pk}/publish", permissions=[ObjectPermissions(perms_map={"POST": ["docs.publish_document"]})]
)
def publish(self, request: HttpRequest, document: Instance[Document]) -> Document: ...
```

## Backends

| Backend | Storage | Lists filtered | Install |
|---|---|---|---|
| `GrantsBackend` | `ninja_devx.contrib.grants` (one table, no dependency) | yes, one `EXISTS` subquery | `INSTALLED_APPS += ["ninja_devx.contrib.grants"]` |
| `GuardianBackend` | django-guardian's tables | yes | `pip install "ninja-devx[guardian]"` |
| `DjangoBackend` | whatever your `AUTHENTICATION_BACKENDS` implement | no, checks only | nothing |

The backend is picked in this order: `NINJA_DEVX["OBJECT_PERMISSION_BACKEND"]` (an instance
or an import path), then the grants app when it is installed, then django-guardian when it
is installed, then `DjangoBackend`. Superusers hold every permission.

To let `user.has_perm("docs.change_document", document)` work in the rest of Django
(templates, admin, DRF), add the matching authentication backend:

```python
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "ninja_devx.contrib.grants.backends.GrantBackend",  # or guardian.backends.ObjectPermissionBackend
]
```

!!! note "Migrating from django-guardian"

    Keep your data: guardian is detected automatically, and `assign_perm`, `remove_perm`,
    `get_perms` and `get_objects_for_user` in `ninja_devx.security.object_permissions` delegate to it.
    Set `ANONYMOUS_USER_NAME = None` if you don't use guardian's anonymous user.

## Options

```python
ObjectPermissions(
    perms_map={**DEFAULT_PERMS_MAP, "GET": ["docs.view_document", "docs.read_document"]},
    model_permissions=True,  # a user with docs.change_document globally may edit every document
    filter_lists=True,  # lists only contain viewable objects
    hide_forbidden=True,  # 404 instead of 403 when the object isn't viewable
)
```

- Permission templates use `%(app_label)s` and `%(model_name)s`, like DRF's `perms_map`.
- With `model_permissions=True`, a model-wide permission counts too. For example, support
  staff with `docs.view_document` see every document.
- `DjangoBackend` cannot filter lists. Set `filter_lists=False` and scope the queryset
  yourself, or pick a filtering backend.

## Sharing endpoints

`ObjectSharingMixin` lets clients manage access themselves. It adds three routes; rename or
disable them with `routes`:

```python
class DocumentController(
    ObjectSharingMixin[Document], CRUDController[Document, DocumentOut, DocumentIn]
):
    object_permissions = ObjectPermissions()
    shareable_permissions = ("view", "change")  # what clients may grant
    sharing_permission = "change"  # what the caller needs on the object
```

| Route | Body | Result |
|---|---|---|
| `GET /{pk}/permissions` | — | `[{"user_id", "group_id", "permissions"}]` |
| `PUT /{pk}/permissions` | `{"user_id": 7, "permissions": ["view"]}` (or `group_id`) | replaces that holder's shareable permissions |
| `POST /{pk}/permissions/revoke` | `{"user_id": 7}` | removes them |

Override `validate_holder(request, obj, holder)` to restrict who objects may be shared with,
for example members of the same workspace (raise `HttpError(422, ...)`). The SaaS example
does this.

The object is loaded like `retrieve`. Tenant, parent and view scoping apply, so a caller
who cannot see it gets 404. Without `sharing_permission` the result is 403. Permissions
outside `shareable_permissions` are rejected with 422.

## Shortcuts

```python
from ninja_devx.security.object_permissions import (
    assign_perm,
    get_objects_for_user,
    get_perms,
    grants_for,
    remove_perm,
)

assign_perm("docs.change_document", bob, document)
remove_perm("docs.change_document", bob, document)
get_perms(bob, document, ["docs.view_document", "docs.change_document"])  # the ones Bob holds
get_objects_for_user(bob, "docs.view_document", Document.objects.all())
grants_for(document)  # [Grant(user_id=..., group_id=None, permissions=(...))]
```

Grant permissions in the service that creates the object, in the same transaction:

```python
class DocumentService(ModelService[Document]):
    @dual
    def create(self, data: Mapping[str, object]) -> Document:
        document = super().create(data)
        for action in ("view", "change", "delete"):
            assign_perm(f"docs.{action}_document", data["owner"], document)
        return document
```

## Admin

`ObjectGrant` rows are listed in the Django admin (filter by content type, search by
object key), so support staff can inspect and remove grants.

## Async

Checks work the same in async controllers. The user is loaded first, the permission
check runs in one thread hop, and list filtering is part of the queryset (no extra
query).

## Performance

- `GrantsBackend` loads a user's permissions on an object with one query and caches them
  on the user object, which usually lives for one request.
- List filtering adds one `EXISTS` subquery per required permission to the list query.
  The migration creates an index on `(content_type, object_pk)`.
- `assign_perm` and `remove_perm` clear the cache of the user object they receive.
