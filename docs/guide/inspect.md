# Inspecting controllers

!!! tip "Reference"

    Every option on this page, with types and defaults: [configuration reference](../options/commands.md#managepy-devx_inspect).

`devx_inspect` prints the policy a controller actually resolves to — scoping, permissions,
transaction, pagination, relations and operations. It reads the live controller and its
settings, so the output is what runs at request time.

```bash
python manage.py devx_inspect app.api.PostController   # by controller path
python manage.py devx_inspect /v1/posts                 # by mounted route prefix
python manage.py devx_inspect --json                    # every configured controller, as JSON
```

Without a target it inspects every controller of the APIs in `NINJA_DEVX["CHECK_APIS"]`
(see [system checks](checks.md)), or every router built in this process. The route-prefix
form requires `CHECK_APIS`, because only a mounted API knows its prefixes; without it the
command explains what to configure.

```text
ArticleController  (/v1/articles)
├── model: Article
├── scope: request
├── mode: sync
├── permissions: IsAuthenticated, IsOwner
├── pagination: PageNumberPagination
├── relations
│   └── prefetch_related: tags
└── operations
    ├── GET /  list
    ├── GET /{pk}  retrieve
    ├── POST /  create
    ├── PUT /{pk}  update
    ├── PATCH /{pk}  partial_update
    ├── DELETE /{pk}  destroy
    └── GET /{pk}/summary  summary
```

Each operation line lists its methods, path, name and any non-default policy: async mode,
permissions, `atomic`, idempotency and declared error rules.

The relations section is the same plan the N+1 optimizer applies; `related` hints and
`@requires_related` lookups are included. See [CRUD](crud.md#n1-queries).

## From Python

The same data is available to tests and scripts through `ninja_devx.tooling.inspect`:

```python
from ninja_devx.tooling.inspect import as_dict, inspect_target, render

(inspection,) = inspect_target("app.api.PostController")
assert "IsOwner" in inspection.permissions
print(render(inspection))          # the tree shown above
policy = as_dict(inspection)       # the --json shape
```

`inspect_target` accepts the same targets as the command and returns one
`ControllerInspection` per mount. Assert on it next to `assert_max_queries` in
[testing](testing.md) when a policy must not drift.
