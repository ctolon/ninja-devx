# Testing

!!! tip "Reference"

    Every option on this page, with types and defaults: [configuration reference](../options/testing.md).

## pytest plugin

The plugin loads automatically.

```python
def test_list(ninja_client) -> None:
    client = ninja_client(PostController, user=ada, container=container)  # every request as ada
    assert client.get("/").status_code == 200
    assert client.get("/", user=bob).status_code == 200  # or per request


async def test_async(ninja_async_client) -> None:
    response = await ninja_async_client(PostController).get("/")


def test_schema(openapi_snapshot) -> None:
    openapi_snapshot(api)  # __snapshots__/<module>/openapi.json
```

Run `pytest --update-snapshots` to accept schema changes.

## Without pytest

`ninja_devx.testing.clients.client_for(Controller, user=...)` / `async_client_for(...)` build Ninja test
clients for a controller.

```python
with assert_max_queries(2):
    client.get("/")
```

```python
with container.override(PaymentGateway, FakeGateway()):
    client.post("/orders/", json=...)
```

## Thread hops and commits

```python
async def test_create_is_one_hop(ninja_async_client) -> None:
    with assert_max_hops(1) as hops:
        await ninja_async_client(NoteController, user=ada).post("/", json={"text": "x"})
    print(hops.functions)  # which sync functions ran in threads


def test_receipt_is_sent_after_commit(ninja_client, captured_commits) -> None:
    with captured_commits() as callbacks:
        ninja_client(OrderController, user=ada).post("/", json=order)
    assert len(callbacks) == 1  # and they ran at the end of the block
```

Async tests that use the database need `@pytest.mark.django_db(transaction=True)`. The
ORM runs on another thread and connection, outside the test transaction. The plugin
warns when it is missing; disable the warning with `ninja_devx_warn_async_db = false` in
the pytest ini.

## Contract tests from OpenAPI

```python
import schemathesis


@pytest.fixture
def api_schema(ninja_contract, db):
    return ninja_contract(api)


schema = schemathesis.pytest.from_fixture("api_schema")


@schema.parametrize()
def test_api_contract(case):
    case.call_and_validate(headers={"Authorization": "Bearer test-token"})
```

With the `contract` extra (`pip install ninja-devx[contract]`), schemathesis generates
valid and invalid requests for every operation. It checks that responses match the
documented status codes, content types and schemas. Requests go through Django's WSGI app
in-process, inside the test transaction. Filter with
`from_fixture("api_schema").include(path_regex="/orders")`.

This is how ninja-devx found that `{int:pk}` path converters turned invalid ids into
Django's HTML 404. Converters are now opt-in (`lookup_converter`).

## Layers without HTTP

`ninja_devx.layers.testing` has `InMemoryRepository(Model)`, `RecordingTaskQueue` and
`make_context(user, tenant)` for unit tests of services and use cases
([Layers](layers.md#testing-the-layers)).

## Local bounded validation

Run branch coverage without changing CI configuration:

```bash
uv run --with coverage python tools/verify_local.py --output /tmp/devx-validation --timeout 180
# An explicit project-owned threshold is optional:
uv run --with coverage python tools/verify_local.py --output /tmp/devx-validation --fail-under 90
```

The tool writes JSON and browsable HTML coverage reports outside the source tree. It
terminates the test process group on timeout and returns 124. The default measures
coverage without inventing a percentage gate; review missing branches in security,
transaction and cancellation paths as well as the aggregate. Backend integration tests
remain opt-in via `TEST_DATABASE_URL`, `TEST_REDIS_URL` and `TEST_S3_ENDPOINT_URL` with
matching S3 test credentials. Use disposable test services and databases.

Build and smoke-test every optional dependency in isolated environments:

```bash
uv build --out-dir /tmp/devx-dist
python tools/verify_package.py /tmp/devx-dist/ninja_devx-0.0.2-py3-none-any.whl \
  --report /tmp/devx-dist/extras.json
```

The verifier checks core, each extra separately, and all extras together, including
installed imports, migrations, locale resources and a routed request. It requires `uv`
and package-index access; it does not claim every supported Python/Django combination
has been tested.

The local critical-path gate defaults to 80% combined statement/branch coverage for the
permission evaluators, permission leaf, DI engine, idempotency policy/store, write scope,
webhook outbox and streaming lifecycle. Use `--critical-under` to set a stricter project
floor (or 0 for a focused test run). This is a regression floor, not proof of security.

## Built-in HTTP contract coverage

The test suite checks transport details alongside operation results:

| Surface | Status / representation / header checks | Regression modules |
|---|---|---|
| CRUD and conditional reads/writes | JSON 200/201/404/422/412/428, empty 204/304, ETag and strong If-Match | `test_builtin_http_contract`, `test_conditional`, `test_crud` |
| Bulk and transfer | 201/204/422, duplicate rejection, import 413, CSV/JSONL media and attachment headers | `test_crud_features`, `test_transfer`, `test_review_security` |
| API keys and grants | authentication/scope errors, paginated JSON, create/revoke and sharing payloads | `test_apikeys`, `test_object_permissions` |
| Audit and webhooks | bounded JSON histories, ownership, retry and delivery results | `test_audit`, `test_webhooks` |
| Uploads | sign/complete JSON, exact size/checksum, 409 overwrite and 410 expiry | `test_uploads`, `test_upload_lifecycle`, `test_s3_upload_integration` |
| Readiness, throttle and idempotency | 503 bounded errors, 429 retry metadata, required-key 400, 409 conflict, replay headers | `test_health_otel_renderers`, `test_throttling`, `test_idempotency` |
| Async streams | real ASGI 401/403/404 before headers, disconnect cleanup | `test_stream_preflight_asgi` |
| Generated clients | status/media dispatch, CSV/binary/204, headers and multipart boundary | `test_codegen_http_contract`, `test_codegen_transfer` |

These contracts cover the built-in configurations under test. Custom response schemas,
renderers, authentication backends and operation overrides need application-level tests.

## Test data

`ninja_devx.testing` builds valid request payloads from a schema: declared defaults and
examples win, otherwise values are derived from the field types. This keeps request bodies
out of tests.

```python
from ninja_devx.testing import sample, samples

payload = sample(ArticleIn)          # {"title": "title", "published": False, ...}
rows = samples(ArticleIn, 3)         # three distinct payloads
```

For model instances, use `factory_boy` or fixtures; `InMemoryRepository` and
`make_context` cover the [layers](layers.md) without a database.
