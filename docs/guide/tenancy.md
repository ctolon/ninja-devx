# Multi-tenancy

!!! tip "Reference"

    Every option on this page, with types and defaults: [configuration reference](../options/crud.md).

SaaS APIs repeat the same code in every view: filter by the current organization, set it
on create, and make sure nobody can reach another tenant's rows. `tenant_field` does all
of it:

```python
class ProjectController(CRUDController[Project, ProjectOut, ProjectIn]):
    tenant_field = "organization"
```

- Every query goes through `scoped_queryset`, which filters `organization=<tenant>`:
  list, retrieve, update, delete, bulk operations, restore and your own `get_object`
  calls.
- Create sets `organization` from the request.
- An input schema that accepts `organization` is a startup error. So is a tenant path
  through a relation (`"project__organization"`) that create cannot fill.
- Another tenant's object is a 404, never a 403, so its existence doesn't leak.
- A request without a tenant gets `403 {"code": "tenant_required"}`.

## Where the tenant comes from

The first configured source wins:

| Source | Example |
|---|---|
| `tenant_resolver` on the controller | `tenant_resolver = staticmethod(org_from_header)` |
| `NINJA_DEVX["TENANT_RESOLVER"]` | `"project.tenancy.org_from_subdomain"` |
| `tenant_context` on the controller, or `NINJA_DEVX["TENANT_CONTEXT"]` | a `RequestContext[User, Org]` key resolved from the container |
| `request.tenant` | set by tenant middleware (django-tenants and similar) |

```python
def org_from_header(request: HttpRequest) -> Organization | None:
    return Organization.objects.filter(
        members=request.user, slug=request.headers.get("X-Org")
    ).first()


async def aorg_from_header(request: HttpRequest) -> Organization | None: ...
```

A resolver may be `async def`. Async operations await it; a sync resolver runs in one
thread hop, before the unit of work. The tenant is resolved once per request and cached:
`current_tenant(request)` returns it to services, hooks and permissions.

With `RequestContext`, the same tenant reaches services outside HTTP:

```python
container.scoped(RequestContext[User, Organization], request_context(User, tenant=org_of))


class ProjectController(CRUDController[Project, ProjectOut, ProjectIn]):
    tenant_field = "organization"
    tenant_context = RequestContext[User, Organization]
```

## Nested resources

```python
class TaskController(CRUDController[Task, TaskOut, TaskIn]):
    tenant_field = "project__organization"
    parent = Parent(Project, field="project", tenant_field="organization")


mount(api, {"/projects/{project_pk}/tasks": TaskController})
```

`Parent(..., tenant_field=...)` only finds parents of the current tenant, so a task can't
be created under another organization's project. The child query is scoped through the
relation.

## Rate limits per tenant

`TenantRateThrottle("10000/day")` counts per tenant ([Throttling](throttling.md)).
