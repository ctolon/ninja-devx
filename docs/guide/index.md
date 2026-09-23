# Guides

Task-oriented explanations. Every option they mention is also listed, with types and
defaults, in the [configuration reference](../options/index.md).

| I want to… | Read |
|---|---|
| expose endpoints from a class, with typed options | [Controllers](controllers.md) |
| decide who may call what | [Permissions](permissions.md) |
| turn business exceptions into responses | [Errors](errors.md) |
| version or mount many controllers | [Mounting and versioning](mounting.md) |
| build list/detail/write endpoints for a model | [CRUD](crud.md) |
| create a parent and its child collections in one request | [Nested writes](nested-writes.md) |
| add named state moves with permissions, guards and a 409 | [Transitions](transitions.md) |
| cap or filter what `?expand=` loads, or see query counts | [Query tuning](query-tuning.md) |
| add a metadata endpoint, aggregates or versioned responses | [API surface](api-surface.md) |
| isolate customers in a SaaS | [Multi-tenancy](tenancy.md) |
| send 304s and prevent lost updates | [HTTP caching and locking](conditional.md) |
| hide fields from some roles, or let clients pick fields | [Field visibility](visibility.md) |
| give users access to single objects, share them | [Object permissions](object-permissions.md) |
| add request ids, deprecation headers, health checks, faster JSON | [Middleware and operations](middleware.md) |
| pick a throttle storage, log one line per request, or mask secrets | [Operations](operations.md) |
| authenticate scripts and integrations with scoped keys | [API keys](api-keys.md) |
| record who changed what | [Audit log](audit.md) |
| notify other systems reliably | [Webhooks](webhooks.md) |
| run work in the background and poll its status | [Jobs](jobs.md) |
| accept large files without streaming them through the API | [Direct uploads](uploads.md) |
| inject services and repositories | [Dependency injection](dependency-injection.md) |
| keep business logic out of HTTP code | [Services and layers](layers.md), [Architecture recipes](recipes.md) |
| separate writes from reads, add domain events | [CQRS and DDD](cqrs.md) |
| write async endpoints without surprises | [Async and sync](async.md) |
| log, trace, run in transactions, make retries safe | [Hooks, transactions, idempotency](hooks.md) |
| rate limit users, scopes or tenants | [Throttling](throttling.md) |
| configure project-wide defaults | [Settings](settings.md) |
| answer clients in their language | [Translations](i18n.md) |
| test controllers, queries, thread hops and contracts | [Testing](testing.md) |
| generate schemas, controllers and clients | [Code generation](codegen.md) |
| scaffold a whole runnable project | [Starting a project](startproject.md) |
| catch configuration and schema mistakes in CI | [System checks](checks.md) |
| see a controller's resolved policy and operations | [Inspecting controllers](inspect.md) |
| catch N+1 queries at runtime and beyond `manage.py check` | [N+1 detection and devx_doctor](doctor.md) |
| get the most out of mypy and pyright | [Typing](typing.md) |
