# N+1 detection and devx_doctor

Two independent tools: `ninja_devx.contrib.nplusone` catches N+1 queries at runtime (an
adapter over [django-zeal](https://github.com/taobojlen/django-zeal)), and
`manage.py devx_doctor` finds configuration risks in mounted controllers that
`manage.py check` does not, such as an unindexed filter field or a list endpoint with no
pagination.

## Runtime N+1 detection

Install the extra and add `"zeal"` to `INSTALLED_APPS`:

```bash
pip install ninja-devx[zeal]
```

```python
INSTALLED_APPS = [..., "zeal"]
```

Add the plugin so every request runs inside zeal's tracking context:

```python
from ninja_devx.contrib.nplusone import NPlusOnePlugin
from ninja_devx.plugins import install

install(api, [NPlusOnePlugin()])
```

Or add just the middleware, to one controller or router:

```python
from ninja_devx.contrib.nplusone import NPlusOneMiddleware

ControllerOptions(middleware=[NPlusOneMiddleware()])
```

- Detection stays entirely in `zeal`; nothing here re-implements query counting.
- When zeal raises `NPlusOneError`, the middleware rewrites the message to name the
  relation and suggest the fix in `ninja_devx.crud.optimization` terms:

  ```text
  N+1 detected on blog.Article.author at blog/api.py:42 in list
  Fix in ArticleController.list: add related = ('author',) to the controller, or
  @requires_related('author') on the resolver that reads it (see
  ninja_devx.crud.optimization).
  ```

- Tune detection with zeal's own settings: `ZEAL_RAISE` (raise instead of warn, default
  on), `ZEAL_NPLUSONE_THRESHOLD`, `ZEAL_ALLOWLIST`. See its README for `zeal_ignore()` and
  the `nplusone_detected` signal.
- Everything in `ninja_devx.contrib.nplusone` is a no-op when `zeal` is not installed, so
  the plugin is safe to add unconditionally.

### Failing tests on N+1

```python
def test_list_has_no_n_plus_one(strict_queries, ninja_client):
    ninja_client(ArticleController).get("/")
```

`strict_queries` (a pytest fixture, auto-loaded with `ninja_devx`) runs the test inside
zeal with `ZEAL_RAISE` forced on, regardless of the project's own setting, and skips with
a clear reason when `zeal` is not installed.

## `manage.py devx_doctor`

```text
$ python manage.py devx_doctor
warn  ArticleController    Article.title is filtered, searched or ordered on without a database index
warn  ArticleController    the list endpoint has no pagination_class configured
info  ProjectController    tenant_field 'organization' is set, but no tenant_resolver/tenant_context is configured on the controller or NINJA_DEVX
warn  NoteController       Note.slug is globally unique, but NoteController soft-deletes rows: a deleted row's value can never be reused
```

It inspects the same mounted controllers as `manage.py check`
([System checks](checks.md)), through `NINJA_DEVX["CHECK_APIS"]`, and reports:

| Severity | Finding |
|---|---|
| warn | a `search_fields`/`filter_fields`/`ordering_fields`/`default_ordering` field has no database index, unique constraint, or is not a primary key or foreign key |
| warn | `owner_field` is set, but `IsOwner` is not enforced and `object_permissions` is not configured |
| info | `tenant_field` is set, but no `tenant_resolver`/`tenant_context` is configured on the controller or `NINJA_DEVX` |
| warn | an output field is filled by a `resolve_<field>()` the N+1 planner cannot analyse, and no `related`/`@requires_related` hint covers it |
| warn | a list endpoint has no `pagination_class` configured |
| warn | a controller has no permission configured at all |
| warn | a `SoftDeleteMixin` model has a globally unique field with no `soft_delete_unique` constraint |

Pass a controller path or a mounted route prefix to check just one:

```bash
python manage.py devx_doctor blog.api.ArticleController
python manage.py devx_doctor /v1/articles
```

- `--json` emits the findings as a list of `{severity, controller, message, hint}` objects
  instead of the table.
- `--fail-on warn` exits 1 when a `warn` (or more severe) finding exists; `--fail-on info`
  exits 1 on any finding. Without it, `devx_doctor` always exits 0 and is safe to run
  informationally in CI.
- Findings are informational: nothing here is wrong the way a `manage.py check` error is,
  which is why it is a separate command instead of another system check.

Add a project-specific finding by appending a function to `ninja_devx.tooling.doctor.CHECKS`:

```python
from ninja_devx.tooling.doctor import CHECKS, Finding


def check_has_a_changelog(target):
    if not getattr(target.controller, "changelog", None):
        yield Finding("info", target.inspection.controller, "no changelog set", "add one")


CHECKS.append(check_has_a_changelog)
```
