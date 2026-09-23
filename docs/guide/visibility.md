# Field visibility

!!! tip "Reference"

    Every option on this page, with types and defaults: [configuration reference](../options/permissions.md#visibleto).

One output schema, with fields shown only to the callers allowed to see them:

```python
class EmployeeOut(FieldVisibility, Schema):
    id: int
    name: str
    salary: Annotated[Decimal | None, VisibleTo(IsStaff())] = None
    email: Annotated[str | None, VisibleTo(IsStaff() | IsOwner("user"), hidden="omit")] = None
```

- `VisibleTo` takes the same permissions as operations, including `&`, `|` and `~`.
  Each permission counts as its request check *and* its object check. So with
  `IsStaff() | IsOwner("user")`, only staff and the owner see the field.
- A hidden field is serialized as `null` (the default) or left out
  (`hidden="omit"`).
- Declare these fields optional (`T | None = None`), so OpenAPI and generated clients
  know they may be missing. System check `ninja_devx.W005` warns otherwise.

## With and without the mixin

| | plain `Schema` | `FieldVisibility, Schema` |
|---|---|---|
| request-level rules (`IsStaff`, `HasDjangoPermission`) | yes | yes |
| object-level rules (`IsOwner`, `as_permission(policy, User)`) | hidden (fails closed) | yes |
| `hidden="omit"` | serialized as `null` | omitted |

Checks run while Ninja serializes the response, using the request Ninja passes to
pydantic. They work the same for lists and nested schemas. Serialization is synchronous;
async CRUD operations load the user beforehand, so the checks don't block.

## Sparse fieldsets and expansion

Let clients ask for less, or more, of the same schema:

```python
class ArticleOut(FieldVisibility, Schema):
    id: int
    title: str
    body: str
    author: Annotated[int | AuthorOut, Expandable()]


class ArticleController(CRUDController[Article, ArticleOut, ArticleIn]):
    sparse_fields = True
```

| Request | `author` | Fields |
|---|---|---|
| `GET /articles/1` | `7` | all |
| `GET /articles/1?expand=author` | `{"id": 7, "username": "ada"}` | all |
| `GET /articles/?fields=id,title` | — | `id`, `title` |
| `GET /articles/?fields=id,author&expand=author` | embedded | `id`, `author` |

- `?fields=` (with `sparse_fields = True`) and `?expand=` (whenever the output schema has
  `Expandable` fields) are added to `list` and `retrieve`. They are documented in
  OpenAPI with the allowed names.
- Unknown names get 422, like any other invalid query parameter.
- The query follows the request. An unexpanded relation reads only its foreign key column
  (`author_id`), without a join. `?expand=author` adds the `select_related` (or
  `prefetch_related` for many-valued relations).
- Unexpanded foreign keys and one-to-one fields render `<field>_id`. For other relations,
  or to render something else, name the attribute: `Expandable(source="author_uuid")`
  or a property such as `Expandable(source="tag_ids")`.
- Field visibility still applies: `?fields=salary` returns nothing more than the caller
  may see.
- Rename the parameters with `fields_param` and `expand_param`. The schema needs the
  `FieldVisibility` mixin, which is checked at startup.

In OpenAPI, `author` is `integer | AuthorOut`, so generated clients handle both forms.
With `sparse_fields`, `list` and `retrieve` respond with `<Out>Partial`: the same
properties, none required. Validating clients (such as `devx_openapi --format python`)
therefore accept `?fields=` responses. Writes keep returning the full schema.

## Write-side visibility

The same idea for input: `WriteVisibleTo` marks an input field only some callers may set.
A create or update that submits the field without permission is rejected with 403 before
anything is persisted.

```python
from typing import Annotated
from ninja_devx import IsStaff, WriteVisibleTo

class ArticleIn(Schema):
    title: str
    featured: Annotated[bool, WriteVisibleTo(IsStaff())] = False
```

- Use request-level permissions (`IsStaff`, `HasDjangoPermission`, a request policy).
  Object-level rules fail closed, because the object is not known when the payload arrives.
- The check runs in `create`, `update` and `partial_update`; `PATCH` only rejects the fields
  actually sent. Use it to protect roles, moderation flags or billing fields from clients.
- `WriteVisibleTo` lives next to `VisibleTo` and takes the same `&`, `|`, `~` combinations.
