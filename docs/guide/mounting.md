# Mounting and versioning

!!! tip "Reference"

    Every option on this page, with types and defaults: [configuration reference](../options/controllers.md#mount).

```python
mount(api, {"/posts": PostController, "/tags": TagController}, container=container)
```

`mount` builds a router per entry with a shared `container`, `scope` and options.
`Mount(controller, container=..., scope=..., options=...)` overrides them per entry.

```python
from ninja_devx import Mount, mount

mount(
    api,
    {
        "/posts": PostController,
        "/legacy": Mount(CompatController, options={"deprecated": True}, container=other),
    },
    prefix="/v1",
    container=container,
)
```

Fine-grained helpers:

| Need | Use |
|---|---|
| One controller | `api.add_router("/posts", PostController.as_router(container=c))` |
| Share a container across controllers | `mount(api, {"/posts": PostController, "/tags": TagController}, container=c)` |
| Override one entry | `Mount(TagController, container=other, scope=Scope.SINGLETON)` |
| Rename or disable an operation | `routes = {"destroy": {"enabled": False}, "list": {"path": "/all"}}` on the controller |
| Mount on a parent router | `mount(parent_router, {...}, prefix="/admin")` |
| Keep plain Ninja routers | they keep working next to controllers |

`Controller.as_router(**options)` returns a new native `ninja.Router` every call, so the
same controller can be mounted on several APIs or prefixes with different containers.

## Versions

```python
ROUTES = {"/posts": PostController, "/tags": TagController}
mount(api, ROUTES, prefix="/v1", deprecated=True)
mount(api, ROUTES, prefix="/v2")
```

With a prefix, operation ids become `v1_post_controller_list` and URL names `v1_...`, so
OpenAPI and `reverse()` stay unambiguous. `deprecated=True` marks every operation of that
version in OpenAPI. Set `allow_mixed_path=True` on the controller to silence the warning
when a path is served by both a sync and an async operation.

To preview what a mounted controller resolves to, run
[`devx_inspect`](inspect.md).
