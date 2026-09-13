# Permissions

!!! tip "Reference"

    Every option on this page, with types and defaults: [configuration reference](../options/permissions.md).

Authentication is Ninja's `auth=`. Permissions decide what the caller may do:

```python
class IsPublished(BasePermission[Post]):
    message = "Not published yet."

    def has_object_permission(self, request: HttpRequest, obj: Post, /) -> bool:
        return obj.status == Post.Status.PUBLISHED


@get("/{pk}", permissions=[IsAuthenticated(), IsOwner("author") | IsStaff() | IsPublished()])
def retrieve(self, request: HttpRequest, post: Instance[Post]) -> Post: ...
```

- Permissions run after authentication and validation, before the controller is built. A
  denial raises `HttpError(permission.status_code, permission.message)`: 403 by default and
  401 for `IsAuthenticated`.
- A list requires every permission and reports the first denial. `&`, `|` and `~` build
  immutable `AllOf`, `AnyOf` and `Not` objects that are safe to share between threads.
- Object permissions run in `get_object`, in `Instance[...]`, or when you call
  `self.check_object_permissions(request, obj)`. Lists are not filtered per object: use
  `scope_queryset_to_owner` or `get_queryset`.
- `BasePermission[T]` is contravariant: a `BasePermission[Model]` guards any model, while
  `IsPublished` only type-checks on posts.
- `async def has_permission` works on async operations; on sync operations it is a startup
  error.
- On async streaming operations, permissions and bindings are checked before response
  headers; denials become regular HTTP errors ([Streaming](async.md#streaming)).
- Operation `permissions=[...]` replace the controller's. `permissions=Also(IsStaff())`
  adds to them.

## Built-in permissions

| Permission | Allows |
|---|---|
| `AllowAny`, `DenyAll` | everyone, no one |
| `IsAuthenticated` | `request.auth` is set, or `request.user` is authenticated (401) |
| `IsAuthenticatedOrReadOnly` | safe methods for everyone |
| `IsReadOnly` | safe methods only (combine: `IsReadOnly() \| IsStaff()`) |
| `IsStaff`, `IsSuperuser` | user flags |
| `HasDjangoPermission("app.perm", ...)` | Django permissions |
| `DjangoModelPermissions()` | `view/add/change/delete` for the controller's model by HTTP method |
| `IsOwner("field")` | object-level: `obj.<field>` is the current user; `"project__owner"` follows relations |
| `as_permission(policy, User)` | object-level: a service-layer `Policy` ([Layers](layers.md#policies)) |

## Typed users

```python
@get("/me", response=UserOut)
def me(self, request: AuthedRequest[User]) -> User:
    return request.auth
```

`current_user(request, User)` returns the user or raises 401, and `acurrent_user` is the
async version. `container.scoped(User, authenticated_user(User))` injects the user into
services. `request_context(User)` builds a `RequestContext` for them.

## Combining request and object rules

For an object, each permission leaf must pass both its request check and its object
check. `IsStaff() | IsOwner()` therefore means staff or the actual owner; a failed
request branch cannot reappear as an allowed object branch. Before lookup, object
checks are unknown and remain unknown through `~`, `&`, and `|`. Such decisions are
deferred until `get_object`, `Instance`, or `check_object_permissions` receives an
object. Object rules do not filter lists: configure queryset scoping for list access.
Sync and async operations use the same Boolean semantics.
