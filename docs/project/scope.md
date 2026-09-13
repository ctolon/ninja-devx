# Scope and design rationale

ninja-devx is an application layer for Django Ninja. It provides controllers and reusable
policies for teams maintaining several Django APIs with similar ownership, persistence,
error and dependency-lifetime rules. It uses Django's ORM and Ninja's schemas, routing and
OpenAPI generation. It is not a replacement web server, ORM or authentication provider.

## The engineering problem

A small API can express an operation in a function: validate an input, select a row, call
a service and return a schema. As an API grows, the same decisions recur across endpoints:

- Which tenant, parent and owner constrain this query?
- Which checks apply to a list, a detail request and a write?
- Where does a transaction begin, and which database owns it?
- Who closes a request-scoped dependency after an exception or disconnect?
- Which business errors become public responses, with which status and shape?
- Does a nested response add one query per result? Does an export apply the same policy?
- Which changes require regenerated clients or a migration note?

Repeated endpoint code is not itself a defect. The maintenance risk is that equivalent
operations gradually implement different policies. A new endpoint may omit an owner filter;
a bulk path may bypass a service; a stream may outlive its dependency scope. Code review
must then rediscover the same rules in every handler.

The package makes those shared decisions explicit in controller configuration, hooks and
persistence interfaces. A controller's model and schemas define its resource; tenant,
owner and parent settings define its data boundary; operations declare permissions and
response types. Registration compiles this configuration and rejects combinations the
package can identify as invalid. Runtime checks still enforce request-dependent rules.

This reduces policy duplication. It does not remove the need for application authorization
tests, database constraints or review of custom handlers.

## Responsibilities

| Concern | Package responsibility | Application responsibility |
|---|---|---|
| HTTP adaptation | Compile controller operations into Ninja routers; bind typed arguments and declared responses | URL design, public compatibility and client rollout |
| Authorization | Compose permission checks; apply configured owner, tenant, parent and object-grant restrictions | Authenticate identities, resolve authorized tenants, define roles and sharing rules |
| Persistence | Default model service/repository, selected-alias transactions and post-write scope checks | Domain invariants, constraints, custom service behavior and external effects |
| Representation | Explicit input/output schemas, field visibility and schema-derived relation loading | Decide which data may be disclosed; provide loading hints for arbitrary resolvers |
| Concurrency | Conditional row writes, durable idempotency ownership and outbox delivery claims | Choose business conflict semantics, retry policy and storage topology |
| Dependencies | Typed resolution with singleton/request/transient lifetimes and cleanup | Register factories; keep request state out of singleton fields |
| Operations | Readiness checks, structured hooks, audit/webhook/upload extensions | Deploy workers, retain records, manage keys, monitor backlog and restore data |
| Maintenance | Generated references, scaffolding checks, client generation and test helpers | Review generated code, run checks and maintain the application's API contract |

## Architecture and coupling

A request enters a Ninja operation. The controller invocation opens its dependency scope,
runs configured hooks and permissions, resolves bindings, and calls the handler. A model
handler obtains a scoped queryset or delegates a write to a service. Ninja validates and
serializes the result. Cleanup follows the invocation's lifecycle, including exceptions
and async stream cancellation.

The implementation separates registration (`routing/compiler.py`) from invocation
(`routing/invocation.py`). Query boundaries live in `crud/scoping.py`; write database and
transaction ownership live in `crud/writes.py`. Dependency contracts, container registration,
resolution state and execution are separate modules. Mutable caches belong to their source
class or application/router owner instead of a process-wide registry.

The public facade is intentionally smaller than the implementation tree. Applications
should use documented root symbols and the CRUD, layers and contrib APIs, not compiler or
resolution-engine internals. See the [stability policy](support.md).

Ninja compatibility still requires testing. In particular, async stream preflight uses
an operation-specific subclass at the Ninja integration boundary, so auth/permission
failures can return an HTTP error before stream headers. “Returns a Ninja router” does
not mean the package is independent of Ninja implementation changes.

## Service layers are optional

For a resource with straightforward rules, `CRUDController[Model, Out, In]` and Django
constraints may be sufficient. Add `perform_*` hooks for a small application-specific
persistence step. Use a service when the same business operation is called by an API,
management command and background task. Introduce repository protocols or use cases when
substitution and independent domain tests justify the extra interfaces.

A service layer should own an actual business operation, such as placing an order with
stock and payment rules. A class that merely forwards every ORM method creates another
maintenance surface without isolating a policy. The [recipes](../guide/recipes.md) show
three supported arrangements over the same models; they are alternatives, not required
layers in every application.

## Security boundaries

Tenant scoping assumes the tenant resolver has authenticated the caller's membership.
A tenant header is a selector, not proof of membership. Object-level permission checks
and list filtering are separate operations. Field visibility affects responses; it does
not automatically redact audit history, webhook payloads or application logs.

Built-in writes verify the persisted result against the configured scope before committing.
Custom persistence hooks are trusted code: a second database, storage upload or remote API
call cannot be undone by the first database's rollback. Use an outbox or an explicit
compensation protocol for such operations. Durable idempotency rejects an uncertain claim
instead of assuming that an interrupted request had no effect. Webhook delivery is
at-least-once; receivers must deduplicate.

These distinctions are part of the public behavior, not deployment details to infer from
an example. See [security](security.md), [CRUD boundaries](../guide/crud.md#persistence-and-database-boundaries)
and the individual contrib guides.

## Cost and limitations

Registration adds compilation and configuration checks. Requests with permissions, DI,
transactions or post-write checks perform more work than a function that omits them.
Schema loading reduces recognizable N+1 patterns but cannot infer arbitrary Python
resolver access. Async Django still requires sync boundaries for transactional ORM work.
The [performance guide](performance.md) reports workloads and measurement limits; it is
not a claim that all applications become faster.

Generated clients support a documented OpenAPI subset. Cookie parameters, SSE clients and
complex multipart encodings are not generated; unsupported cases fail explicitly. The
contrib modules also require operating procedures and, where documented, database migrations
and optional dependencies. They do not constitute a complete identity, billing or storage
platform.

## Adoption criteria

Consider this package when a Django Ninja application has recurring resource policies,
needs typed controllers or optional services, and can accept an alpha API with migration
work between minor versions. Start by porting one resource with meaningful permissions and
contract tests; measure the change in query count and request behavior.

Keep plain Ninja functions when they remain easier to understand and policy duplication is
small. Keep DRF when existing serializers, renderer negotiation, authentication integrations
and extension packages provide more value than a migration would. Consider Ninja Extra
when its controller and Injector conventions match the application. The
[comparison](comparison.md) describes these choices without treating an extension point
as an absent capability.
