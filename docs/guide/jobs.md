# Jobs

!!! tip "Reference"

    [configuration reference](../options/contrib.md#jobs-ninja_devxcontribjobs)

Work that must not block a response — exports, imports, sending a batch of emails — runs
in the background. `ninja_devx.contrib.jobs` tracks each run as a `Job` row, so clients poll
its status instead of holding a connection open.

```python
INSTALLED_APPS += ["ninja_devx.contrib.jobs"]  # then: manage.py migrate
```

## Starting a job

```python
from ninja_devx.contrib.jobs.runner import JobContext, accepted, job, start_job


@job("reports.export")
def export_report(ctx: JobContext, report_id: int) -> dict[str, object]:
    rows = Report.objects.filter(pk=report_id).count()
    ctx.set_progress(50)
    if ctx.cancelled():
        return {"cancelled": True}
    write_report_file(report_id)
    return {"rows": rows}


class ReportController(Controller):
    @post("/{pk}/export", response={202: dict})
    def export(self, request: HttpRequest, pk: int) -> JsonResponse:
        row = start_job(request, "Export report", export_report, report_id=pk)
        return accepted(request, row, prefix="/jobs")
```

`POST /reports/42/export` returns:

```http
HTTP/1.1 202 Accepted
Location: https://api.example.com/jobs/6f9c...

{"id": "6f9c...", "status": "queued", "url": "https://api.example.com/jobs/6f9c..."}
```

- `start_job` creates the `Job` row (`queued`, `created_by` the caller) and enqueues
  `run_job` through a `TaskQueue`: the container's registered singleton if there is one,
  otherwise `OnCommitTaskQueue()`. Pass `queue=` to use another one (Celery, RQ...).
- `@job("reports.export")` registers the function under a stable name. `run_job` resolves
  it by that name, or by a dotted import path for a plain module-level function, never by
  pickling it, so the call survives process restarts and framework upgrades.
- `*args`/`**kwargs` passed to `start_job` are recorded on the row (JSON-normalized) and
  forwarded to the function after its `JobContext`.
- `accepted(request, row, prefix="/jobs")` builds the 202 response: a `Location` header
  and a body with `id`, `status` and `url`, matching wherever `JobsController` is mounted.

## Polling

```python
api.add_router("/jobs", JobsController.as_router())
```

```bash
curl https://api.example.com/jobs/6f9c...
# {"id": "...", "status": "running", "progress": 50, "result": {}, "error": "", ...}
```

The client polls `GET /jobs/{id}` until `status` leaves `queued`/`running`:

| Route | Description |
|---|---|
| `GET /` | the caller's own jobs, paginated (20 per page, 100 max) |
| `GET /{id}` | one job; the owner or staff |
| `POST /{id}/cancel` | mark a queued or running job cancelled; the owner or staff |

Cancelling is cooperative: it flips `status` to `cancelled` immediately, but a running
function only stops if it calls `ctx.cancelled()` between steps. `run_job` never overwrites
a cancellation with a later success or failure.

## Inside `run_job`

```python
run_job(job_id, "reports.export", retry, report_id=42)
```

- Sets `status = running`, `started`, and increments `attempts`, then calls the function
  with a fresh `JobContext`.
- `ctx.set_progress(0..100)` updates `progress` immediately, so polling clients see it
  mid-run.
- An exception is retried in the same call up to `retry` times, then stored as `error`
  with `status = failed`. `retry` is not part of `start_job`'s signature; pass it when
  wiring `run_job` into your own task framework, or use `devx_jobs retry` below.
- On success, `status = succeeded`, `progress = 100`, and the return value (JSON
  normalized) is stored as `result`.

## Housekeeping

```bash
manage.py devx_jobs prune --older-than 30
manage.py devx_jobs retry 6f9c1a2b-...
```

`prune` deletes terminal jobs (`succeeded`, `failed`, `cancelled`) older than the given
number of days. `retry` replays a failed or cancelled job synchronously, in the current
process, with the function and arguments recorded when it was started; `--retry-count`
gives it its own in-process retry budget (default 0).

## Admin

With `django.contrib.admin` installed, jobs are listed with their status, progress and
owner; rows cannot be created there (only through `start_job`), and the resolved function
and arguments are read-only.
