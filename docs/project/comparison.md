# Comparison

The choice is primarily about the application's existing contracts and where its policies
live. This page compares DRF, Django Ninja, Django Ninja Extra and ninja-devx as application
libraries. “Application code” means the framework supplies extension points but the
specific policy must be implemented or obtained from another package. It does not mean
that the behavior is impossible.

The upstream columns describe their documented interfaces, checked on 13 September 2026.
The ninja-devx column describes the 0.0.1 alpha source. No cross-framework latency or
security ranking is implied. Third-party DRF/Ninja packages can change the comparison.

## Programming and compatibility model

| Decision | Django REST framework | Django Ninja | Django Ninja Extra | ninja-devx |
|---|---|---|---|---|
| HTTP abstraction | `APIView`, generic views and ViewSets | Typed functions on an API/router | Decorated controller classes and routes | Controller methods compiled into Ninja routers |
| API object | Django URLconf and DRF routers | `NinjaAPI` | `NinjaExtraAPI` | `NinjaAPI`; existing function routers can coexist |
| Input/output definitions | Serializer and ModelSerializer classes | Pydantic-based Schema and ModelSchema | Ninja schemas; model-controller configuration | Explicit Ninja input/output schemas or opt-in model generation |
| Request access | DRF Request, `request.data`, view state | Django HttpRequest parameter, typed arguments | Controller route context | Explicit request parameter; request/operation metadata helpers |
| Persistence entry point | Serializer `create/update`, generic-view hooks | Application function/service | Model controllers and services | Default ModelService/ModelRepository; replaceable `perform_*` hooks |
| Configuration | View attributes, routers and framework settings | Decorator arguments and Django settings | Controller decorators, ModelConfig, framework settings | Typed operation options, generic model/schema arguments and class settings |
| Dependency injection | Application convention or external library | Application convention | Injector integration | Built-in container; optional dishka/svcs adapters |
| Existing Django integration | ORM, middleware and authentication ecosystem | ORM and Django middleware | Ninja plus controller integrations | Ninja plus optional persistence/contrib policies |

Sources: [DRF ViewSets](https://www.django-rest-framework.org/api-guide/viewsets/),
[DRF serializers](https://www.django-rest-framework.org/api-guide/serializers/),
[Ninja routers](https://django-ninja.dev/guides/routers/),
[Ninja Extra controllers](https://eadwincode.github.io/django-ninja-extra/api_controller/).

## Resource policy

| Concern | DRF | Ninja | Ninja Extra | ninja-devx |
|---|---|---|---|---|
| Standard CRUD | Generic views and ModelViewSet | Application handlers | ModelControllerBase/ModelConfig | CRUDController and individual mixins |
| PATCH | Serializer partial validation | Application schema/update logic | Configurable model route | PatchData retains which fields were submitted |
| Model validation | Serializer validation and declared validators | Schema validation; persistence is application code | Schema/model-controller behavior | Schema validation plus default model `full_clean`; configurable |
| List query restriction | `get_queryset` and filter backends | Application queryset | Controller/query configuration | Composed tenant, owner, parent and optional object-grant filters |
| Object checks | Object permissions in generic detail lookup; custom views call checks | Application policy | Permission classes and object checks | `get_object`/Instance bindings and configured permissions; persisted write result rechecked |
| List authorization | Queryset filtering is separate from object permission checks | Application code | Queryset/filter policy | Explicit owner/tenant settings and optional grant-backed filtering |
| Owner/tenant assignment | `perform_create`, serializer defaults or service | Application code | Controller/service policy | Context fields set from request; custom service/hook must preserve policy |
| Nested resources | URL/query configuration or an extension package | URL parameters plus application queries | Controller prefix parameters plus query policy | Parent binding validates and scopes parent/child access |
| Soft delete | Model/view/service or an extension | Application code | Application/model customization | Configurable soft-delete mixin and restore route |
| Bulk writes | ListSerializer/custom view or an extension | Application code | Custom routes/services | Bounded per-row validated operations within a transaction |
| Search/filter/order | Filter backends; django-filter is a common integration | FilterSchema and application ordering | Searching/ordering/pagination decorators | Typed filter and ordering generation or explicit FilterSchema |
| Pagination | Page, limit/offset and cursor classes | Pagination decorators/classes | Ninja-compatible pagination and extra classes | Ninja classes plus optional cursor/offset implementations |
| Related-object loading | Explicit queryset `select_related/prefetch_related` | Explicit queryset loading | Queryset/model-controller configuration | Schema-derived loading with explicit hints for arbitrary resolvers |

Sources: [DRF generic views](https://www.django-rest-framework.org/api-guide/generic-views/),
[DRF permission boundaries](https://www.django-rest-framework.org/api-guide/permissions/),
[DRF pagination](https://www.django-rest-framework.org/api-guide/pagination/),
[Ninja Extra model controllers](https://eadwincode.github.io/django-ninja-extra/api_controller/model_controller/).
The presence of a generic controller does not establish an application's tenant policy.

## HTTP, lifecycle and operations

| Concern | DRF | Ninja | Ninja Extra | ninja-devx |
|---|---|---|---|---|
| Authentication | Authentication-class ecosystem, session/token integrations | Auth callables/classes and Django auth integration | Ninja auth plus extra authentication support | Ninja auth; optional scoped API-key module |
| CSRF/session behavior | Determined by authentication class and request flow | Determined by Ninja auth and Django middleware | Must verify the chosen extra/Ninja authentication flow | Same verification required; controller permissions do not replace CSRF |
| Response negotiation | Parser/renderer/content-negotiation framework | Declared schemas and renderer | Ninja response stack | Ninja responses; optional renderers and problem-detail mapping |
| Domain errors | Exception handler customization | Exception handlers | Ninja/extra handlers | Declared ErrorMap and DomainError mapping |
| OpenAPI | Schema tooling; extension choices matter | Types and operation definitions | Controller routes generate Ninja OpenAPI | Compiled operation metadata; typed client generation for a documented subset |
| Async application handlers | Check chosen DRF interfaces/extensions | Async operations | Async controller operations | Sync/async variants, dependency cleanup and transactional sync boundaries |
| Streaming | Django streaming responses and application lifecycle | Streaming support | Ninja/route integration | Async preflight integration; cancellation tests; generator-body errors remain late |
| Transactions | Django atomic blocks/ATOMIC_REQUESTS and application design | Django/application design | Service/application design | Built-in selected-alias write scopes; custom effects still application-owned |
| Conditional updates | Application code or extension | Application code | Custom route/service | Strong If-Match, row-locked built-in updates, 412/428 |
| Idempotency | Application infrastructure or extension | Application infrastructure | Application infrastructure | Durable owner-token claim and response replay; requires autocommit or separate alias |
| Audit history | Application/extension | Application/extension | Application/extension | Optional redacted audit records; bounded staff/role-protected history |
| Webhooks | Application/extension | Application/extension | Application/extension | Optional transactional outbox, destination checks, signatures and retry/retention tools |
| Upload confirmation | Application/storage integration | Application/storage integration | Application/storage integration | Optional issued-upload records, size/checksum/version checks and expiry cleanup |
| Tests/tooling | APIClient, request factory and established testing ecosystem | TestClient/TestAsyncClient | Extra test clients | Ninja clients plus DI overrides, query/hop assertions, drift and generated-client checks |

Sources: [DRF authentication](https://www.django-rest-framework.org/api-guide/authentication/),
[Ninja authentication](https://django-ninja.dev/guides/authentication/),
[Ninja responses](https://django-ninja.dev/guides/response/),
[Ninja async support](https://django-ninja.dev/guides/async-support/),
[Ninja Extra](https://eadwincode.github.io/django-ninja-extra/).
Package-specific details and limitations are in the [guides](../guide/index.md).

## When each choice is reasonable

**Stay with DRF** when serializer behavior, browsable responses, renderer negotiation or
existing integrations are part of the product contract. Replacing them involves client
and operational migration, not just translating a ViewSet. A typed request schema alone
is not a sufficient reason to rewrite a functioning API.

**Use plain Ninja** when typed functions express the service clearly and shared policy is
small. An explicit queryset and service call can be easier to review than a generic
controller configuration. Existing Ninja applications can adopt ninja-devx one router at
a time while retaining the rest of the API.

**Use Ninja Extra** when controller discovery, its model-controller conventions and
Injector match the team. Migrating an established extra application also means replacing
permission instances, request context and dependency lifetimes; there is no automatic
compatibility adapter.

**Consider ninja-devx** when repeated resource and lifecycle rules have become a maintenance
cost and the team wants those rules in typed configuration with optional services. The
tradeoff is an additional alpha dependency, framework integration testing, generated-client
constraints and operational responsibility for contrib features. See
[scope and design rationale](scope.md) for boundaries and
[performance](performance.md) for measured workloads rather than a framework ranking.

Migration guides: [DRF](../migration/drf.md), [plain Ninja](../migration/ninja.md),
[Ninja Extra](../migration/ninja-extra.md).
