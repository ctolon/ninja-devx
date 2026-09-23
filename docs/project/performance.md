# Performance

Everything that can be decided at registration is decided at registration: signatures,
generic arguments, models and schemas, permissions, hooks, error rules, bindings and
dependency plans. Operations that use none of the optional features get a direct view
with no context managers.

## Overhead per request

`benchmarks/overhead.py` sends the same requests to plain Ninja function views and to
controllers through Ninja's test clients: routing, validation and serialization, with no
server. Cases run interleaved and the fastest of several rounds counts, which reduces
some scheduling noise. Ratios still depend on the machine and dependency versions.

| Case | µs / request | vs function view | Thread hops |
|---|---:|---:|---:|
| function view, sync | 47.4 | | 0 |
| controller, request scope + DI | 56.7 | 1.20x | 0 |
| function view, async | 48.4 | | 0 |
| controller, async, request scope + DI | 62.8 | 1.30x | 0 |
| function view, sync list (20 rows) | 266.4 | | 0 |
| `ReadOnlyModelController` list | 321.0 | 1.21x | 0 |
| function view, async list | 307.9 | | 1 |
| `AsyncReadOnlyModelController` list | 416.5 | 1.35x | 1 |

Measured on Python 3.13, Django 6.1 and django-ninja 1.7 with SQLite, 5 rounds × 2000
requests. The model controller rows include filters, ordering, owner scoping hooks and
the N+1 planner.

```bash
uv run python benchmarks/overhead.py
uv run python benchmarks/overhead.py --check benchmarks/budget.json   # optional local budget check
```

## Budget

The optional `--check` command fails when a controller exceeds the ratios or thread-hop
limits in `benchmarks/budget.json`. CI does not enforce timing ratios on shared runners.
The regular test suite checks thread hops with `assert_max_hops`, including the single-hop
contract for async CRUD writes. Recalibrate timing budgets on a stable runner before
using them as a release gate.

## Real servers

```bash
uv sync --group bench
uv run python benchmarks/load.py                                  # gunicorn (WSGI) and uvicorn (ASGI)
uv run python benchmarks/load.py --concurrency 1 50 200 --duration 10 --workers 4
BENCH_DATABASE_URL="postgresql://bench:bench@localhost/bench?pool=1" uv run python benchmarks/load.py
```

Each server, endpoint and concurrency level reports requests per second and p50/p99
latency. Controller endpoints sit next to their function-view twins. PostgreSQL runs with
Django's connection pool when `?pool=1` is set. The load generator is a single Python
process, so it saturates at about a thousand requests per second. Use `oha` or `wrk`
against the same `benchmarks.wsgi` / `benchmarks.asgi` apps for higher rates.

## Cross-framework overhead

`benchmarks/frameworks/overhead.py` serves the same `GET /items` payload through Django
Ninja, ninja-devx and (when installed) Django REST framework and django-ninja-extra, and
reports per-request overhead. It characterizes the abstraction each framework adds; it is
not a ranking and is not a CI gate.

```bash
python benchmarks/frameworks/overhead.py
uv run --isolated --with djangorestframework python benchmarks/frameworks/overhead.py
```

See `benchmarks/frameworks/README.md`.

## Database queries

The CRUD endpoints derive `select_related`, `Prefetch` querysets and optionally `only()`
from the output schema ([N+1 queries](../guide/crud.md#n1-queries)). Listing articles with
nested authors, and comments with their own nested authors, runs two queries whatever the
number of rows. Guard your endpoints with `assert_max_queries(n)`.

## Async

Django's ORM is synchronous underneath, so async endpoints pay per thread hop rather than
per query. See [Async and sync](../guide/async.md) for keeping a request to one hop.

## Related-data workload

```bash
uv run python benchmarks/data_paths.py --rows 10000 --samples 20 --output /tmp/data-paths.json
```

This workload always creates and removes its own temporary SQLite database, ignoring
`BENCH_DATABASE_URL`. It combines a schema resolver, nested comments, `VisibleTo`, object
grants, JSONL export and duplicate bulk validation with rollback. The JSON records the
first request, warm p50/p95/p99, query counts, response bytes and process peak RSS.

Observed locally on Python 3.13.15 / Django 6.1.1, 10,000 articles, 30,000 comments and
10,000 grants, with 20 warm samples (13 September 2026):

| Operation | First ms | Warm p50 / p95 / p99 ms | Queries | Response bytes |
|---|---:|---:|---:|---:|
| Related list + resolver + visibility + grants | 480.1 | 533.4 / 576.5 / 583.4 | 2 | 1,637,784 |
| Same scope, JSONL export | 554.2 | 516.1 / 537.1 / 550.8 | 6 | 1,487,784 |
| Duplicate bulk validation + rollback | 1.27 | 0.70 / 0.83 / 3.28 | 15 | 142 |

Peak process RSS was 199,924 KiB including setup and earlier cases. First-request timing
is not a flushed OS/database cache measurement. Twenty samples give only a coarse tail
estimate; repeat on representative hardware and a production-like database before setting
latency gates. Exports consume the full response in the harness, so RSS also includes the
client's buffered body. The query count covers complete stream consumption. The six
export queries are one root query plus five related-data prefetch batches, not one query
per article. Arbitrary resolver dependencies still need explicit `select_related` hints.
