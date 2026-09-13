# System checks and schema drift

!!! tip "Reference"

    Every option on this page, with types and defaults: [configuration reference](../options/commands.md#system-checks).

## `manage.py check`

With `"ninja_devx"` in `INSTALLED_APPS`, Django's system checks inspect every mounted
controller. Checked controllers are the ones behind `NINJA_DEVX["CHECK_APIS"]`
(`["config.api.api"]`), or, when that is empty, every router `as_router()` built while
loading the URLconf.

| Id | Problem |
|---|---|
| `ninja_devx.E001` | a `CHECK_APIS` entry cannot be imported or is not a `NinjaAPI` |
| `ninja_devx.E002` | `search_fields`, `filter_fields`, `ordering_fields` or `default_ordering` names a missing field |
| `ninja_devx.W003` | an output schema field is not a model field or attribute, and the schema does not resolve it |
| `ninja_devx.E004` | `service_class` needs constructor arguments, but the controller has no container |
| `ninja_devx.W005` | an output field that `VisibleTo` can hide is required |

Mistakes that make a controller unusable, such as a bad `owner_field` or an unresolvable
`Inject[T]`, raise at `as_router()` instead.

Add project rules by overriding the class method (keep the built-ins with `super()`):

```python
from django.core.checks import CheckMessage, Warning


class ProjectController(Controller):
    @classmethod
    def checks(cls, container: ContainerLike | None = None) -> list[CheckMessage]:
        messages = super().checks(container)
        if not cls.options.get("tags"):
            messages.append(Warning(f"{cls.__qualname__} has no tags", id="project.W001"))
        return messages
```

Plugins can contribute too by defining `checks(controller)`.

## `manage.py devx_scaffold --check`

Schemas written by hand (or generated once by `devx_scaffold`) drift as models change.
The check compares every mounted `ModelController`'s schemas with its model:

```text
$ python manage.py devx_scaffold --check
NoteController  NoteOut.priority  error  model IntegerField is int, schema says str
NoteController  NoteOut.deleted_at  error  the model allows NULL but the schema does not
NoteController  NoteIn  error  required field 'title' is not accepted; creating fails
NoteController  NoteIn.priority  warning  the schema accepts null, the model does not
CommandError: 3 schema drift error(s)
```

- **Errors** are values the model can hold that the output schema would reject (type,
  `NULL`, choices), input the model rejects (unknown fields, values outside the choices),
  and required model fields that create cannot fill. The owner and parent fields count as
  filled.
- **Warnings** flag input that is accepted but would fail on save: `null` for a non-null
  column, or strings longer than the column's `max_length`.
- `-v 2` also lists model fields the output schema does not expose.

Pass a model (`devx_scaffold --check blog.Post`) to check only its controllers. Run it in
CI next to `makemigrations --check`.
