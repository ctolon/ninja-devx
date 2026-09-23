"""Long-running work started from a request and finished by a worker.

``INSTALLED_APPS += ["ninja_devx.contrib.jobs"]``, then::

    from ninja_devx.contrib.jobs.api import JobsController
    from ninja_devx.contrib.jobs.runner import JobContext, accepted, job, start_job

    @job("reports.export")
    def export_report(ctx: JobContext, report_id: int) -> dict[str, object]:
        ctx.set_progress(50)
        return {"rows": 1000}

    class ReportController(Controller):
        @post("/{pk}/export", response={202: dict})
        def export(self, request: HttpRequest, pk: int) -> JsonResponse:
            row = start_job(request, "Export report", export_report, report_id=pk)
            return accepted(request, row, prefix="/jobs")

    api.add_router("/jobs", JobsController.as_router())

A worker (``django.tasks``, Celery, RQ... any ``TaskQueue``) resolves the function by
the name it was registered or imported with, never by pickling it, so the call survives
process restarts and framework upgrades. Clients poll ``GET /jobs/{id}`` (the
``Location`` header of the 202) until the job leaves ``queued``/``running``.
"""
