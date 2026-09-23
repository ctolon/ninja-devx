# Quickstart

A private notes API with search, filters, ordering, pagination, ownership and a custom
action. The code below is `examples/quickstart`, included verbatim and tested in CI.

To start from a generated project instead of reading along, see
[`manage.py devx_startproject`](../guide/startproject.md).

## 1. A model

```python title="notes/models.py"
--8<-- "examples/quickstart/notes/models.py"
```

## 2. A controller

```python title="notes/api.py"
--8<-- "examples/quickstart/notes/api.py"
```

- `CRUDController[Note, NoteOut, NoteIn]` gives list, retrieve, create, update, partial
  update and delete. The generic arguments are the configuration.
- `owner_field = "owner"` requires authentication, sets the owner on create, and checks
  ownership on every object.
- `Instance[Note]` loads the note from the URL, with a 404 when it is missing and the
  object permissions applied.

## 3. Mount it

```python title="config/urls.py"
--8<-- "examples/quickstart/config/urls.py"
```

`mount()` builds a native Ninja router per controller. `python manage.py runserver` serves
the interactive docs at `/api/docs`.

## 4. Test it

```python title="notes/tests/test_api.py"
--8<-- "examples/quickstart/notes/tests/test_api.py"
```

## What you got

| Endpoint | Behavior |
|---|---|
| `GET /api/notes/?search=&done=&ordering=&page=` | the caller's notes, searched, filtered, ordered and paginated |
| `POST /api/notes/` | 201; `owner` set from the request; `title` validated (1–200 characters) |
| `GET/PUT/PATCH/DELETE /api/notes/{pk}` | 404 for other users' notes |
| `POST /api/notes/{pk}/complete` | a custom action with the same guarantees |
| OpenAPI | documents 401, 403, 404, 422 and the typed query parameters |

## Next steps

=== "Async"

    ```python
    NINJA_DEVX = {"ASYNC_MODE": "async"}  # every CRUD operation registers its async version
    ```

    See [Async and sync](../guide/async.md) and `examples/async_api`.

=== "Multi-tenant"

    ```python
    class NoteController(CRUDController[Note, NoteOut, NoteIn]):
        tenant_field = "workspace"
    ```

    See [Multi-tenancy](../guide/tenancy.md) and `examples/saas`.

=== "Services"

    ```python
    class NoteController(CRUDController[Note, NoteOut, NoteIn]):
        service_class = NoteService  # writes go through your business rules
    ```

    See [Services and layers](../guide/layers.md) and `examples/recipes`.

For everything else — permissions, tenancy, errors, hooks and the optional `contrib`
apps — start at the [guides index](../guide/index.md). To see how a controller actually
resolves, run [`devx_inspect`](../guide/inspect.md).
