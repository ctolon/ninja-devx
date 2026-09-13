# Mounting and versioning

!!! tip "Reference"

    Every option on this page, with types and defaults: [configuration reference](../options/controllers.md#mount).

```python
mount(api, {"/posts": PostController, "/tags": TagController}, container=container)
```

`mount` builds a router per entry with a shared `container`, `scope` and options.
`Mount(controller, container=..., scope=..., options=...)` overrides them per entry.

## Versions

```python
ROUTES = {"/posts": PostController, "/tags": TagController}
mount(api, ROUTES, prefix="/v1", deprecated=True)
mount(api, ROUTES, prefix="/v2")
```

With a prefix, operation ids become `v1_post_controller_list` and URL names `v1_...`, so
OpenAPI and `reverse()` stay unambiguous. `deprecated=True` marks every operation of that
version in OpenAPI.
