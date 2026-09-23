"""Checking on and cancelling jobs started with ``start_job``."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils import timezone
from ninja import Schema
from ninja.errors import AuthenticationError
from ninja.pagination import PageNumberPagination, paginate

from ...crud.annotations import Instance
from ...routing.controller import Controller, ControllerOptions
from ...routing.operations import get, post
from ...security.auth import request_user
from ...security.permissions import Also, IsAuthenticated, IsOwner, IsStaff
from .models import Job

__all__ = ["JobOut", "JobsController"]


class JobOut(Schema):
    id: UUID
    """Primary key, also the value ``start_job``/``accepted`` return in the ``Location``."""
    name: str
    """Recorded label, shown in the API and admin."""
    status: str
    """One of ``Job.Status``: ``queued``, ``running``, ``succeeded``, ``failed``, ``cancelled``."""
    progress: int
    """Last value reported through ``JobContext.set_progress`` (0 to 100)."""
    result: dict[str, object]
    """The function's return value, once ``succeeded``."""
    error: str
    """The failure message, once ``failed``."""
    attempts: int
    """Times the job has started running, including retries."""
    created: datetime
    """When ``start_job`` created the row."""
    started: datetime | None
    """When the job most recently started running."""
    finished: datetime | None
    """When the job reached a terminal status."""


class JobsController(Controller):
    """``GET /`` (the caller's jobs, paginated), ``GET /{id}`` and ``POST /{id}/cancel``.

    Mount next to the endpoints that call ``start_job``::

        api.add_router("/jobs", JobsController.as_router())

    ``GET /{id}`` and cancelling are open to the job's owner or staff; the list only ever
    shows the caller's own jobs.
    """

    options = ControllerOptions(permissions=[IsAuthenticated()], tags=["jobs"])

    def get_queryset(self, request: HttpRequest) -> QuerySet[Job]:
        user = request_user(request)
        if user is None:
            raise AuthenticationError()
        return Job.objects.filter(created_by=user).order_by("-created", "-pk")

    @get(
        "/",
        response=list[JobOut],
        summary="The caller's jobs",
        decorators=[paginate(PageNumberPagination, page_size=20, max_page_size=100)],
    )
    def list(self, request: HttpRequest) -> QuerySet[Job]:
        return self.get_queryset(request)

    @get("/{pk}", response=JobOut, permissions=Also(IsOwner("created_by") | IsStaff()))
    def retrieve(self, request: HttpRequest, job: Instance[Job]) -> Job:
        return job

    @post(
        "/{pk}/cancel",
        response=JobOut,
        permissions=Also(IsOwner("created_by") | IsStaff()),
        summary="Cancel a queued or running job",
    )
    def cancel(self, request: HttpRequest, job: Instance[Job]) -> Job:
        if job.status in {Job.Status.QUEUED, Job.Status.RUNNING}:
            Job.objects.filter(pk=job.pk).update(
                status=Job.Status.CANCELLED, finished=timezone.now()
            )
            job.refresh_from_db()
        return job
