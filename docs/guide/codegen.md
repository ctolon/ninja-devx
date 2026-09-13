# Code generation

!!! tip "Reference"

    Every option on this page, with types and defaults: [configuration reference](../options/commands.md).

## New apps

```bash
python manage.py devx_startapp billing
```

This is Django's `startapp` with a ninja-devx template. It creates `api.py` (a controller
with a service injected), `schemas.py`, `services.py`, `models.py`,
`apps.py`, `migrations/` and `tests/test_api.py`, then prints the next steps. It accepts
every `startapp` option, including your own `--template`.

## Scaffold

```bash
python manage.py devx_scaffold app.Model [--owner FIELD] [--read ...] [--write ...] \
    [--async] [--output PATH] [--tests PATH] [--no-tests] [--force] [--print]
```

Generates `ModelOut` / `ModelIn` schemas, a controller and tests as plain, typed Python.
Input schemas carry the model's rules as pydantic constraints, so bad input gets a 422
from Ninja instead of a database error:

```python
class ArticleIn(Schema):
    title: Annotated[str, Field(max_length=200, min_length=1)]
    slug: Annotated[str, Field(max_length=50, min_length=1, pattern="^[-a-zA-Z0-9_]+$")]
    price: Annotated[Decimal, Field(max_digits=8, decimal_places=2, ge=0)]  # MinValueValidator(0)
    body: str = ""
```

- `max_length`, `blank=False` → `min_length=1`, positive integer fields → `ge=0`,
  `max_digits`/`decimal_places`, `MinValueValidator`/`MaxValueValidator`/
  `MinLengthValidator` and regex validators are translated. The database backend's
  integer ranges are skipped.
- `help_text` becomes the field's `description` (and OpenAPI's).
- `JSONField` becomes pydantic's `JsonValue`.

- nullable fields become `T | None`
- choices become `Literal[...]`
- foreign keys become `<name>_id`
- many-to-many fields become `list[pk]` with a resolver
- defaults are kept
- search, filter and ordering fields are suggested

The output passes mypy strict, and the generated tests pass. Once the schemas are yours,
`devx_scaffold --check` reports when they drift from the models
([System checks](checks.md#managepy-devx_scaffold-check)).

## OpenAPI and clients

```bash
python manage.py devx_openapi config.urls.api                       # JSON schema
python manage.py devx_openapi config.urls.api --format typescript --output web/api.ts
python manage.py devx_openapi config.urls.api --format python --output clients/api.py
python manage.py devx_openapi config.urls.api --format python --output clients/api.py --check
```

- **TypeScript:** interfaces for every schema, plus `createClient({ baseUrl, headers, fetch })`
  with one typed function per operation. Checked with `tsc --strict`.
- **Python:** `ApiClient` (httpx) and `AsyncApiClient`, raising `ApiError` on non-2xx
  responses. Checked with mypy strict. Two model styles (`--python-style`):
    - `pydantic` (default): `BaseModel` classes with the schema's constraints
      (`Field(max_length=..., pattern=..., ge=...)`), real `datetime`/`date`/`UUID` types,
      defaults, descriptions and aliases for names that aren't identifiers. Bodies are
      sent with `exclude_unset`, so a `PATCH` only sends what you set, and responses are
      validated into models.
    - `typeddict`: `TypedDict` shapes with no runtime validation.

Exclude generated clients from formatters and use `--check` in CI to catch drift.

### Per-request headers and response metadata

Python methods include keyword-only declared header parameters plus `request_headers=`
and `on_response=`. The callback runs synchronously for every HTTP response, before
body decoding, including errors; use it to read status, ETag, Location or retry headers:

```python
metadata = []
item = client.items_retrieve(
    42,
    request_headers={"If-None-Match": previous_tag},
    on_response=lambda response: metadata.append((response.status_code, response.headers)),
)
```

TypeScript methods take a typed `headers` object when the operation declares headers,
followed by `requestOptions = { headers, onResponse }`. Body and query arguments retain
those names; inspect the generated signature for their positions. The callback reads
metadata; it must not consume or replace the response body. No shared `last_response`
state is used, so concurrent calls keep their own metadata.

### Supported OpenAPI subset

- JSON request bodies; path, query and header parameters; path-level parameters and
  local parameter/request-body/response references. Query arrays use repeated form keys.
  Operation parameters override matching path-level declarations.
- Documented 2xx JSON representations are combined into a union. Empty 204 returns
  `None`/`undefined`; text/CSV responses return strings; binary responses return
  `bytes`/`Uint8Array`. Downloads are buffered, so bound their size at the API layer.
- Non-2xx responses raise `ApiError` with status, body and headers. HTML, plain-text and
  malformed JSON error bodies are retained for inspection. Pydantic clients validate
  successful JSON bodies; TypedDict and TypeScript types do not add runtime validation.
- Multipart requests with a required binary file and simple scalar fields are supported.
  Custom multipart encoding, nested/array form fields, other non-JSON bodies, explicit cookie parameters,
  SSE streaming, deepObject/custom styles, query `explode=False`, and external references
  currently raise a generation error. They are never silently omitted, including optional
  parameters. Configure session cookies on the underlying HTTP client/browser.
- Component names must be valid non-reserved target identifiers. Normalized method,
  parameter and Pydantic field collisions fail explicitly. Titles, summaries, aliases and
  static URL segments are quoted as data, including quotes, backticks, newlines and Unicode.
  Python source is AST-validated before returning it; the command preserves an existing
  output file if validation fails. TypeScript safety and HTTP behavior are covered by
  compiler and fetch-mock regression tests.

### Multipart imports and response variants

Inline multipart bodies become named models with a required file field. Python uses
`UploadFile(filename, content, content_type)` (or bytes); TypeScript uses `Blob`/`File`
and builds `FormData`. Do not set the multipart Content-Type manually: the client removes
an override so HTTPX/fetch can supply the boundary.

```python
from generated_client import ApiClient, MultipartBody0, UploadFile

client = ApiClient(base_url="https://api.example.test")
result = client.tags_import_rows(
    MultipartBody0(file=UploadFile("tags.csv", b"name\nexample\n", "text/csv"))
)
```

The generated body model name depends on the document; use the annotation on the
operation. Pydantic clients select response models by actual status and media type, so
200/201 schemas with identical fields still produce the correct model class. CSV is
returned as text; binary exports as bytes/Uint8Array. These helpers buffer successful
export responses; SSE requires a separate streaming client and is rejected during
client generation rather than silently treated as JSON.

Real WSGI/ASGI import/export tests exercise both Python model styles and sync/async
clients. TypeScript tests compile the client and inspect the actual FormData handed to
fetch, including filename, type, content and boundary handling.
