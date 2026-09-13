# Async and sync

!!! tip "Reference"

    Every option on this page, with types and defaults: [configuration reference](../options/settings.md).

Django's ORM is synchronous underneath: every `a`-method is `sync_to_async`, and there
are no async transactions. Within one request, thread-sensitive work runs serially on one
thread. So the cost of async Django is the number of **thread hops** (tens of
microseconds each), and ninja-devx keeps that number low.

## Choosing a mode

Write `async def` operations when the endpoint awaits something that is really async
(HTTP calls, streaming, `asyncio` libraries). Otherwise sync operations under WSGI are
simpler and just as fast.

The generic CRUD controllers implement both. `mode` picks one per controller:

```python
class NoteController(CRUDController[Note, NoteOut, NoteIn]):
    mode = "async"  # "sync", "async" or "auto" (NINJA_DEVX["ASYNC_MODE"])
```

`ModelController` defaults to `"auto"`, so a project switches every CRUD controller with
one setting. `AsyncCRUDController` is `CRUDController` with `mode = "async"`. Operation
ids and OpenAPI are identical in both modes.

Your own reusable bases can do the same with `async_variant`:

```python
class Exports(Controller):
    mode = "auto"

    @get("/export")
    def export(self, request: HttpRequest) -> list[Row]: ...

    @async_variant(export)
    async def aexport(self, request: HttpRequest) -> list[Row]: ...
```

## One hop per unit of work

```python
@post("/{pk}/archive", response=NoteOut)
async def archive(self, request: HttpRequest, pk: int) -> Note:
    return await self.run_atomic(self._archive, request, pk)  # load, check, save: 1 hop


def _archive(self, request: HttpRequest, pk: int) -> Note:
    note = self.get_object(request, pk, lock=True)
    note.archived = True
    note.save(update_fields=["archived"])
    return note
```

- `await self.run_sync(fn, *args)` runs sync code in one hop.
- `await self.run_atomic(fn, *args)` does the same inside `transaction.atomic()`.
- `atomic=True` on an `async def` operation is a startup error that points to
  `run_atomic`. An async transaction cannot span awaits.
- The async CRUD writes (lookup, object permissions, service call and reload) run in a
  single hop. `assert_max_hops(1)` in the test suite keeps it that way.
- `Locked[Model]` (`Instance` with `select_for_update`) loads the object locked in sync or
  atomic operations.

## Async all the way down

- **Permissions** have `ahas_permission`/`ahas_object_permission`. The built-ins load
  the session user with `request.auser()` and use `ahas_perm`. A permission with
  `async def has_permission` is async-only, and using it on a sync operation fails at
  startup.
- **Hooks**: `around_async(request, operation)` returns an async context manager. Sync
  hooks also run in async operations and are assumed not to block. Set `blocking = True`
  on a hook to run its enter/exit in a thread.
- **Lifecycle**: `before_operation`/`after_operation` may be `async def`.
- **Dependency injection**: container factories may be `async def` or async generators
  (with cleanup). Async operations resolve through `aresolve`, and
  `DishkaResolver(..., async_container=...)` serves both modes.
- **Users and context**: `arequest_user`, `acurrent_user`, `aauthenticated_user(User)`,
  `arequest_context(User)`.

## Streaming

Sync and async generators work with Ninja's `SSE[T]` and `JSONL[T]` responses. Hooks and
the DI scope stay open until the stream ends, so a logged duration is the real stream
duration.

Permission checks, hooks, parameter bindings and `before_operation` run before response
headers. Denials and missing objects become regular 401/403/404 responses. Streaming
starts after preflight succeeds, without waiting for the first event.

A producer task owns the async DI scope and hooks for the whole stream. It pulls one
item only when the response consumer requests it; it does not fill an unbounded buffer.
Normal completion, cancellation and response closure release those resources in the
same task/context that opened them. Hook ContextVars remain local to the streaming task.

Put authorization in permissions, bindings or `before_operation`. Errors raised inside
the generator body after preflight cannot change headers that have already been sent.
The Ninja 1.x streaming adapter is isolated in `_internal.streaming`; its subclass is
preserved when Ninja clones routers for mounting.

## Mistakes reported loudly

| Mistake | What happens |
|---|---|
| Lazy relation access in an async operation | `SynchronousOnlyOperation` is re-raised as `AsyncLazyAccessError`, naming the operation and suggesting `select_related` or a schema field |
| The same on Django 6.1+ with `NINJA_DEVX["ASYNC_FETCH_MODE"] = "raise"` | querysets use `fetch_mode(FETCH_RAISE)`: every lazy load fails, not only the ones that happen to run on the event loop |
| Sync code blocking the event loop | `NINJA_DEVX["WARN_BLOCKING_MS"] = 20` warns (`BlockingCallWarning`) when a sync hook or injected parameter blocks longer |
| Sync and async operations on the same path | Ninja runs the sync ones in a thread too: `MixedPathWarning` at startup (`allow_mixed_path=True` to silence) |
| Async test without `django_db(transaction=True)` | the pytest plugin warns (`AsyncDatabaseTestWarning`): the ORM runs on another connection, outside the test transaction |

## Native paths by version

| Django | Used |
|---|---|
| 4.2 | `sync_to_async` hops for users and permissions |
| 5.0+ | `request.auser()` |
| 5.2+ | `ahas_perm` in `DjangoModelPermissions` |
| any | Ninja's async paginators evaluate list pages with the async ORM |
| 6.1+ | `QuerySet.fetch_mode(FETCH_RAISE)` with `ASYNC_FETCH_MODE = "raise"` |

A truly async database backend (for example `django-async-backend`) needs no changes
here: the `a`-methods stop hopping, and `run_sync` stays correct.

## Writing sync code from async code: `unasync`

Libraries and services that need both versions can keep one source. Write the async
implementation and generate the sync twin:

```bash
python -m ninja_devx.tooling.unasync app/aio/orders.py:app/sync/orders.py
python -m ninja_devx.tooling.unasync app/aio/orders.py:app/sync/orders.py --check   # in CI
```

The conversion works on tokens: `async def` → `def`, `await x` → `x`,
`async for/with` → `for/with`. It also renames Django's async ORM methods
(`aget` → `get`), this package's `a`-helpers and the async protocol names.
`--replace AsyncOrders=Orders` adds your own renames. ninja-devx generates its permission
evaluator this way, and a test runs `--check`.

### Older Django ASGI cancellation

Django 4.2 does not listen for a peer disconnect while sending an async stream. The
package closes its producer, dependency scope and request files when the application task
is cancelled, but cannot make that Django handler observe a disconnect event. Use Django
5.2 or later for the tested peer-disconnect path. The compatibility tests distinguish
server-driven cancellation on 4.2 from disconnect propagation on newer Django versions.
