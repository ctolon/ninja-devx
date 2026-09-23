"""Starting, registering and running background jobs.

::

    @job("reports.export")
    def export_report(ctx: JobContext, report_id: int) -> dict[str, object]:
        ctx.set_progress(50)
        return {"rows": 1000}

    @post("/reports/{pk}/export", response={202: dict})
    def export(self, request: HttpRequest, pk: int) -> JsonResponse:
        row = start_job(request, "Export report", export_report, report_id=pk)
        return accepted(request, row, prefix="/jobs")

``start_job`` records the call (function name and arguments) on the ``Job`` row and
enqueues ``run_job`` through a ``TaskQueue``, so nothing is pickled: a worker (or
``devx_jobs retry``) resolves the function by the name it was registered or imported
with. ``@job`` is optional for functions importable by a dotted path; use it for
closures, local functions, or a stable name independent of where the function lives.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import cast

from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import F
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.utils.module_loading import import_string
from ninja.errors import AuthenticationError

from ...dependencies.instances import get_invocation
from ...exceptions import ControllerConfigError
from ...layers.tasks import OnCommitTaskQueue, TaskQueue
from ...security.auth import request_user
from .models import Job

__all__ = ["JobContext", "JobFunction", "accepted", "job", "resolve_job", "run_job", "start_job"]

JobFunction = Callable[..., object]
"""A registered job: ``(ctx: JobContext, *args, **kwargs) -> object``."""

_NAME_ATTR = "__ninja_devx_job_name__"
_REGISTRY: dict[str, JobFunction] = {}


class JobContext:
    """Given to a job function: progress reporting and cooperative cancellation.

    :param job_id: Primary key of the ``Job`` row this context reports on.
    """

    __slots__ = ("job_id",)

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id

    def set_progress(self, percent: int) -> None:
        """Record how far the job has gotten; call from inside the function.

        :param percent: A value from 0 to 100.
        """
        if not 0 <= percent <= 100:
            raise ValueError("percent must be between 0 and 100")
        Job.objects.filter(pk=self.job_id).update(progress=percent)

    def cancelled(self) -> bool:
        """Whether ``POST /{id}/cancel`` was called; long-running functions should check it
        between steps and stop early."""
        status = Job.objects.filter(pk=self.job_id).values_list("status", flat=True).first()
        return status == Job.Status.CANCELLED


def job(name: str) -> Callable[[JobFunction], JobFunction]:
    """Register a function so workers resolve it by name instead of pickling it.

    :param name: The name ``start_job`` and ``run_job`` use to find the function again.
    """

    def decorator(function: JobFunction) -> JobFunction:
        existing = _REGISTRY.get(name)
        if existing is not None and existing is not function:
            raise ControllerConfigError(f"a job is already registered as {name!r}")
        _REGISTRY[name] = function
        setattr(function, _NAME_ATTR, name)
        return function

    return decorator


def resolve_job(target: str) -> JobFunction:
    """The function registered, or importable, as ``target``.

    :param target: A name passed to ``@job``, or a dotted import path.
    """
    function = _REGISTRY.get(target)
    if function is not None:
        return function
    try:
        imported: object = import_string(target)
    except ImportError as exc:
        raise LookupError(f"No job is registered or importable as {target!r}") from exc
    if not callable(imported):
        raise LookupError(f"{target!r} does not resolve to a callable")
    return imported


def start_job(
    request: HttpRequest,
    name: str,
    function: JobFunction | str,
    *args: object,
    queue: TaskQueue | None = None,
    tenant: str = "",
    **kwargs: object,
) -> Job:
    """Create a queued job for the caller and enqueue its run.

    :param request: The current request; ``created_by`` is the authenticated user (401
        without one).
    :param name: Recorded on the job; shown in the API and admin.
    :param function: A ``@job``-registered function, its registered name, or a dotted
        import path. A plain function is recorded by ``module.qualname``.
    :param args: Positional arguments passed to the function after its ``JobContext``.
    :param queue: Where ``run_job`` is enqueued. Default: a ``TaskQueue`` singleton from
        the request's container, else ``OnCommitTaskQueue()``.
    :param tenant: Recorded on the job (server-controlled; not read from the request body).
    :param kwargs: Keyword arguments passed to the function.
    """
    user = request_user(request)
    if user is None:
        raise AuthenticationError()
    target = _target_of(function)
    row = Job.objects.create(
        name=name,
        function=target,
        arguments=_json_safe({"args": list(args), "kwargs": kwargs}),
        created_by=user,
        tenant=tenant,
    )
    (queue or _default_queue(request)).call(run_job, str(row.pk), target, 0, *args, **kwargs)
    return row


def run_job(job_id: str, target: str, retry: int = 0, /, *args: object, **kwargs: object) -> None:
    """Run a job's function inside a ``JobContext``, storing the outcome on its row.

    Never raises: a failure is stored as ``error`` and status ``failed`` instead of
    propagating, so a task framework will not retry the call itself.

    :param job_id: Primary key of the ``Job`` row (also passed to the ``JobContext``).
    :param target: A name registered with ``@job``, or a dotted import path.
    :param retry: Extra attempts on failure, made in this same call before giving up.
    :param args: Positional arguments passed to the function after its ``JobContext``.
    :param kwargs: Keyword arguments passed to the function.
    """
    try:
        function = resolve_job(target)
    except LookupError as exc:
        _finish(job_id, Job.Status.FAILED, error=str(exc))
        return
    context = JobContext(job_id)
    tries = 0
    while True:
        tries += 1
        started = (
            Job.objects.filter(pk=job_id)
            .exclude(status=Job.Status.CANCELLED)
            .update(status=Job.Status.RUNNING, started=timezone.now(), attempts=F("attempts") + 1)
        )
        if not started:
            return
        try:
            result = function(context, *args, **kwargs)
        except Exception as exc:
            if tries <= retry:
                continue
            _finish(job_id, Job.Status.FAILED, error=str(exc))
            return
        _finish(job_id, Job.Status.SUCCEEDED, progress=100, result=_json_safe(result))
        return


def accepted(request: HttpRequest, row: Job, *, prefix: str = "/jobs") -> JsonResponse:
    """A 202 response pointing at the job: a ``Location`` header and a small body.

    :param request: The current request, used to build an absolute URL.
    :param row: The job just started.
    :param prefix: Where ``JobsController`` is mounted.
    """
    location = request.build_absolute_uri(f"{prefix.rstrip('/')}/{row.pk}")
    response = JsonResponse({"id": str(row.pk), "status": row.status, "url": location}, status=202)
    response["Location"] = location
    return response


def _finish(job_id: str, status: str, **fields: object) -> None:
    Job.objects.filter(pk=job_id).exclude(status=Job.Status.CANCELLED).update(
        status=status, finished=timezone.now(), **fields
    )


def _target_of(function: JobFunction | str) -> str:
    if isinstance(function, str):
        return function
    name: object = getattr(function, _NAME_ATTR, None)
    if isinstance(name, str):
        return name
    return f"{function.__module__}.{function.__qualname__}"


def _json_safe(value: object) -> object:
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


def _default_queue(request: HttpRequest) -> TaskQueue:
    invocation = get_invocation(request)
    if invocation is not None and invocation.resolver is not None:
        try:
            resolved = invocation.resolver.resolve(cast("type[object]", TaskQueue))
        except Exception:  # the resolver's own "not registered" error, whatever it is
            pass
        else:
            return cast("TaskQueue", resolved)
    return OnCommitTaskQueue()
