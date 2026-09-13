# Hooks, logging, tracing and transactions

!!! tip "Reference"

    Every option on this page, with types and defaults: [configuration reference](../options/controllers.md).

## Operation hooks

```python
class Timing:
    @contextmanager
    def around(self, request: HttpRequest, operation: OperationInfo, /) -> Generator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            metrics.observe(operation.operation_id, time.perf_counter() - started)


class PostController(CRUDController[...]):
    options = ControllerOptions(hooks=[Timing(), LoggingHook()])
```

- Hooks wrap permission checks and the call, so 401/403/404 pass through them.
- Controller hooks wrap operation hooks.
- On streaming operations, hooks wrap the whole stream.
- `get_operation(request)` returns the `OperationInfo` anywhere during the request.

Async operations prefer `around_async`, which returns an async context manager. A hook
may implement both:

```python
class AuditHook:
    @asynccontextmanager
    async def around_async(
        self, request: HttpRequest, operation: OperationInfo, /
    ) -> AsyncGenerator[None]:
        await audit_log.write(operation.operation_id)
        yield
```

Sync hooks also run in async operations. Set `blocking = True` on those that do I/O, so
their enter and exit run in a thread.

## Typed metadata

```python
@dataclass(frozen=True)
class RequiresScope:
    scope: str


@post("/", meta=(RequiresScope("orders:write"),))
def create(self, request: HttpRequest, payload: OrderIn) -> Order: ...


class HasScope(BasePermission[object]):
    def has_permission(self, request: HttpRequest, /) -> bool:
        operation = get_operation(request)
        required = operation and operation.meta(RequiresScope)
        return required is None or required.scope in token_scopes(request)
```

`meta` combines controller and operation options. `operation.meta(Kind)` returns the
closest item of that type, typed as `Kind | None`.

## Built-in hooks

- `LoggingHook(logger, level)` writes one record per call with `operation_id`,
  `controller`, `http_method`, `path`, `outcome` and `duration_ms`.
- `OpenTelemetryHook(tracer)` from `ninja_devx.contrib.otel` opens a span per call and
  records exceptions.

## Transactions

`atomic=True` (operation or controller option) runs a sync operation in
`transaction.atomic()`, including permissions, bindings and the method.

- `atomic="durable"` requires it to be the outermost transaction.
- `database="replica"` picks the alias.
- In async operations, use `await self.run_atomic(fn, ...)` instead
  ([Async and sync](async.md)).
- Work that must wait for the commit goes through `after_commit(fn)` or an
  `OnCommitTaskQueue` ([Layers](layers.md#after-commit-work)).

## Idempotency

```python
@post("/", response={201: OrderOut}, decorators=[idempotent(ttl=3600)])
def create(self, request: HttpRequest, payload: OrderIn) -> Status[Order]: ...
```

- **Setup:** Install `ninja_devx`, run `manage.py migrate`, and use a durable database
  (`NINJA_DEVX["IDEMPOTENCY_DATABASE"]`, default `"default"`). The ownership connection
  must use autocommit. With `ATOMIC_REQUESTS` or an outer transaction, select a separate
  connection alias. `atomic=True` on the operation starts **after** ownership is committed.
- **Replay:** Completed responses, including errors, are stored for `ttl` seconds and
  replayed with `Idempotent-Replayed: true`. Status, body, media type, ETag, Location and
  application headers are preserved. Cookies, connection headers and trace/request IDs
  are not replayed. A new request after retention expires may execute again.
- **Conflicts:** A different body, query, content type, Accept or language with the same
  key gets 422. A running or uncertain request gets 409. Keys are limited to 255 characters.
- **Scope:** Method, path, authenticated principal, tenant, Authorization, X-API-Key and
  the configured session cookie partition records. For custom non-scalar identities,
  `scope=lambda request: ...` must return a stable identity including both principal
  and tenant. It runs after authentication and controller bindings.
- **Authorization:** Authentication, throttles, request permissions, bindings and
  `before_operation` run on every attempt. `authorize_replay(request, operation, arguments)`
  also runs before acquisition. Model controllers re-fetch URL objects here, enforcing
  current owner, grants, tenant and deletion rules. A deleted object can therefore produce
  404 on retry. Move authorization from inside custom handlers into these preflight hooks.
  Non-model controllers with object permissions must override `authorize_replay` explicitly.
- **Uncertain outcomes:** Cancellation, process failure, serialization/storage failure or
  an unexpected streaming result leaves a durable running claim. It has **no automatic
  lease expiry**. Inspect `IdempotencyRecord` and reconcile the business operation before
  deleting a running record with its matching ownership token. Deleting it permits another
  execution. Never delete running records merely because they are old. This protocol
  prevents automatic duplicate execution; it cannot make arbitrary external side effects
  and a database response record one atomic transaction.
- **Async:** Ownership and response persistence use thread-offloaded database I/O. A
  cancelled caller cannot release a claim while its handler might have committed a write.
- **Limits:** Use this in controller `decorators=`, once per operation. Generator/streaming
  operations are rejected. Response records can contain private data: apply the same
  database access and backup controls as the underlying business records. Prune expired
  **completed** rows periodically; preserve running rows for reconciliation.
