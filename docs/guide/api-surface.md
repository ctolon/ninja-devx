# API surface

Four small additions that make a model controller easier to consume from a UI or another
service: a metadata endpoint, an aggregation endpoint, response versioning, and OpenAPI
examples generated from sample data.

## Field metadata

`MetaMixin` adds `GET /meta`, describing the input and output schemas for building forms
and admin UIs without hard-coding field types:

```python
from ninja_devx.crud import CRUDController
from ninja_devx.crud.meta import MetaMixin


class NoteController(MetaMixin[Note], CRUDController[Note, NoteOut, NoteIn]):
    filter_fields = {"status": ("exact",)}
    ordering_fields = ("priority",)
    search_fields = ("text",)
```

```json
{
  "input": {"text": {"type": "str", "required": true, "read_only": false, "max_length": 100, "choices": null}},
  "output": {
    "status": {
      "type": "str", "required": true, "read_only": true, "max_length": 10,
      "choices": [{"value": "draft", "label": "Draft"}, {"value": "done", "label": "Done"}]
    }
  },
  "filter_fields": ["status"],
  "ordering_fields": ["priority"],
  "search_fields": ["text"]
}
```

- A field is `read_only` when the output schema carries it but the input schema does not.
- Choices come from the matching Django model field's `choices` (labels are converted with
  `str()`, so lazily translated labels resolve to the current language), or from an
  `enum.Enum` type on the schema field itself when there is no matching model field.
- `max_length` comes from the schema field's own constraints first, then the model field.
- The response is a typed `Schema`, so it is in OpenAPI and in generated clients.
- The structure needs no database access; it is computed once and cached per controller
  class. `GET /meta` is registered as a plain sync method, which also works fine on
  controllers running in async mode.

## Aggregation

`AggregateMixin` adds `GET /stats`, grouping and summarizing the same scoped queryset the
list operation would serve:

```python
from django.db.models import Count, Sum
from ninja_devx.crud.aggregates import AggregateMixin


class OrderController(AggregateMixin[Order, OrderOut], CRUDController[Order, OrderOut, OrderIn]):
    aggregate_fields = ("status", "region")
    aggregate_metrics = {"total": Sum("amount"), "count": Count("id")}
```

`GET /stats?group_by=status&metrics=total&metrics=count` answers:

```json
[{"status": "done", "total": "120.00", "count": 3}, {"status": "open", "total": "40.00", "count": 1}]
```

- `group_by` is restricted to `aggregate_fields`; `metrics` to the keys of
  `aggregate_metrics` (default `["count"]`). Either one naming anything else is a 422.
- The queryset is `get_queryset`/`scoped_queryset` filtered by `filter_queryset` with the
  same generated (or explicit) filter schema the list operation uses, so tenancy,
  ownership, soft deletion and `search`/`filter_fields` all apply.
- Omitting `group_by` aggregates the whole queryset into one row.
- Any ordering, including a model's default `Meta.ordering`, is cleared before grouping;
  otherwise it would be pulled into `GROUP BY` and split rows that should be grouped
  together.
- The response is typed loosely as `list[dict[str, object]]`; the actual keys depend on
  the request's `group_by` and `metrics`. Available sync and async.

## Response versioning

`VersionedResponseMixin` lets a controller keep serving an older response shape after the
output schema grows, negotiated by a request header:

```python
from ninja_devx.http.versioning import VersionedResponseMixin


class PostOutV1(Schema):
    id: int
    title: str


class PostController(VersionedResponseMixin, CRUDController[Post, PostOut, PostIn]):
    response_versions = {1: PostOutV1}
```

- A client sending `Accept-Version: 1` gets `PostOutV1` (or `list[PostOutV1]` for list
  operations); omitting the header serves the latest shape (`response=` as declared).
  The version numbers are the schemas a controller used to return; the current shape is
  implicitly one more than the highest key.
- The downgrade re-validates the already-rendered response with the older schema, so
  there is no second database round trip.
- Every response carries `X-API-Version` naming the version actually served; an unknown
  `Accept-Version` answers 406 in the project's error format.
- `Accept-Version`/`X-API-Version` are configurable per controller with
  `response_version_header`/`response_version_response_header`.
- The header is documented as an OpenAPI parameter on every affected operation. Only
  single-schema and `list[...]` responses can be downgraded; other shapes (a status-keyed
  `response=` without one 2xx entry, a plain `dict` body...) still get the header and 406
  handling, but are served unchanged since there is no schema to validate them against.

## OpenAPI examples from sample data

`openapi_examples = True` fills an OpenAPI example into a model controller's input and
output schemas, derived the same way `ninja_devx.testing.sample` builds test payloads:

```python
class PostController(CRUDController[Post, PostOut, PostIn]):
    openapi_examples = True
```

- Built field by field: a field with no derivation rule (no default, no example, and a
  type `sample` does not know how to build) is left out instead of failing the whole
  example.
- Works on any schema directly, too: `PostOut = with_examples(PostOut)` from
  `ninja_devx.serialization.examples`.

## Integration

Combine mixins as usual; order among them does not matter unless two of them override the
same hook:

```python
class PostController(
    VersionedResponseMixin,
    MetaMixin[Post],
    AggregateMixin[Post, PostOut],
    CRUDController[Post, PostOut, PostIn],
):
    response_versions = {1: PostOutV1}
    aggregate_fields = ("status",)
    openapi_examples = True
```
