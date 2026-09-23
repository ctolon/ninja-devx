from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.http import HttpRequest
from django.test import RequestFactory
from django.utils import timezone
from ninja.errors import AuthenticationError
from ninja.testing import TestClient

from ninja_devx.contrib.jobs.api import JobsController
from ninja_devx.contrib.jobs.models import Job
from ninja_devx.contrib.jobs.runner import (
    JobContext,
    accepted,
    job,
    resolve_job,
    run_job,
    start_job,
)
from ninja_devx.exceptions import ControllerConfigError
from ninja_devx.layers import ImmediateTaskQueue, RecordingTaskQueue

pytestmark = pytest.mark.django_db

_rf = RequestFactory()


@job("tests.jobs.add")
def _add(ctx: JobContext, a: int, b: int) -> int:
    ctx.set_progress(50)
    return a + b


@job("tests.jobs.boom")
def _boom(ctx: JobContext) -> None:
    raise ValueError("nope")


@job("tests.jobs.flaky")
def _flaky(ctx: JobContext, calls: list[int]) -> str:
    calls.append(1)
    if len(calls) < 2:
        raise RuntimeError("try again")
    return "ok"


def _plain_add(ctx: JobContext, a: int, b: int) -> int:
    return a + b


NOT_CALLABLE = 42


def _request(user: User) -> HttpRequest:
    request = _rf.get("/")
    request.user = user
    return request


@pytest.fixture
def user() -> User:
    return User.objects.create(username="ada")


@pytest.fixture
def staff() -> User:
    return User.objects.create(username="root", is_staff=True)


def _job_for(user: User, **fields: object) -> Job:
    defaults: dict[str, object] = {"name": "Export", "function": "tests.jobs.add"}
    return Job.objects.create(created_by=user, **{**defaults, **fields})


# --- job / resolve_job -------------------------------------------------------------


def test_job_registers_a_function_by_name():
    assert resolve_job("tests.jobs.add") is _add


def test_job_allows_reregistering_the_same_function():
    job("tests.jobs.add")(_add)


def test_job_rejects_a_different_function_under_the_same_name():
    with pytest.raises(ControllerConfigError, match="already registered"):
        job("tests.jobs.add")(_boom)


def test_resolve_job_imports_a_dotted_path():
    assert resolve_job("tests.test_jobs._plain_add") is _plain_add


def test_resolve_job_rejects_an_unimportable_target():
    with pytest.raises(LookupError, match="registered or importable"):
        resolve_job("nonexistent.module.function")


def test_resolve_job_rejects_a_non_callable_target():
    with pytest.raises(LookupError, match="does not resolve to a callable"):
        resolve_job("tests.test_jobs.NOT_CALLABLE")


# --- JobContext ---------------------------------------------------------------------


def test_job_context_set_progress_updates_the_row(user):
    row = _job_for(user)
    JobContext(str(row.pk)).set_progress(42)
    row.refresh_from_db()
    assert row.progress == 42


def test_job_context_set_progress_rejects_out_of_range_values(user):
    row = _job_for(user)
    with pytest.raises(ValueError, match="0 and 100"):
        JobContext(str(row.pk)).set_progress(101)


def test_job_context_cancelled_reflects_the_stored_status(user):
    row = _job_for(user, status=Job.Status.CANCELLED)
    assert JobContext(str(row.pk)).cancelled() is True
    other = _job_for(user, status=Job.Status.RUNNING)
    assert JobContext(str(other.pk)).cancelled() is False


# --- start_job ------------------------------------------------------------------------


def test_start_job_creates_a_queued_row_and_enqueues_run_job(user):
    queue = RecordingTaskQueue()
    row = start_job(_request(user), "Add", _add, 1, 2, queue=queue)
    assert row.status == Job.Status.QUEUED
    assert row.name == "Add"
    assert row.created_by_id == user.pk
    assert row.function == "tests.jobs.add"
    assert row.arguments == {"args": [1, 2], "kwargs": {}}
    assert len(queue.calls) == 1
    queue.run_all()
    row.refresh_from_db()
    assert row.status == Job.Status.SUCCEEDED
    assert row.result == 3
    assert row.progress == 100


def test_start_job_needs_an_authenticated_user():
    with pytest.raises(AuthenticationError):
        start_job(_rf.get("/"), "Add", _add, 1, 2, queue=ImmediateTaskQueue())


def test_start_job_records_the_tenant(user):
    row = start_job(_request(user), "Add", _add, 1, 2, queue=ImmediateTaskQueue(), tenant="acme")
    assert row.tenant == "acme"


def test_start_job_accepts_a_dotted_path_string(user):
    row = start_job(
        _request(user), "Add", "tests.test_jobs._plain_add", 5, 6, queue=ImmediateTaskQueue()
    )
    row.refresh_from_db()
    assert row.function == "tests.test_jobs._plain_add"
    assert row.status == Job.Status.SUCCEEDED
    assert row.result == 11


@pytest.mark.django_db(transaction=True)
def test_start_job_defaults_to_on_commit_when_no_queue_and_no_container(user):
    row = start_job(_request(user), "Add", _add, 1, 2)
    assert row.status == Job.Status.QUEUED
    row.refresh_from_db()
    assert row.status == Job.Status.SUCCEEDED


# --- run_job --------------------------------------------------------------------------


def test_run_job_stores_the_result_on_success(user):
    row = _job_for(user, arguments={"args": [2, 3], "kwargs": {}})
    run_job(str(row.pk), "tests.jobs.add", 0, 2, 3)
    row.refresh_from_db()
    assert row.status == Job.Status.SUCCEEDED
    assert row.result == 5
    assert row.progress == 100
    assert row.attempts == 1
    assert row.started is not None
    assert row.finished is not None


def test_run_job_stores_the_error_on_failure(user):
    row = _job_for(user, function="tests.jobs.boom")
    run_job(str(row.pk), "tests.jobs.boom", 0)
    row.refresh_from_db()
    assert row.status == Job.Status.FAILED
    assert row.error == "nope"
    assert row.attempts == 1


def test_run_job_retries_before_succeeding(user):
    row = _job_for(user, function="tests.jobs.flaky")
    calls: list[int] = []
    run_job(str(row.pk), "tests.jobs.flaky", 1, calls)
    row.refresh_from_db()
    assert row.status == Job.Status.SUCCEEDED
    assert row.result == "ok"
    assert row.attempts == 2
    assert len(calls) == 2


def test_run_job_fails_after_exhausting_retries(user):
    row = _job_for(user, function="tests.jobs.boom")
    run_job(str(row.pk), "tests.jobs.boom", 2)
    row.refresh_from_db()
    assert row.status == Job.Status.FAILED
    assert row.attempts == 3


def test_run_job_skips_a_job_cancelled_before_it_started(user):
    row = _job_for(user, status=Job.Status.CANCELLED)
    run_job(str(row.pk), "tests.jobs.add", 0, 1, 2)
    row.refresh_from_db()
    assert row.status == Job.Status.CANCELLED
    assert row.attempts == 0


def test_run_job_does_not_overwrite_a_cancellation_made_while_running(user):
    row = _job_for(user, function="tests.jobs.cancel-mid-run")

    @job("tests.jobs.cancel-mid-run")
    def cancel_mid_run(ctx: JobContext) -> str:
        Job.objects.filter(pk=row.pk).update(status=Job.Status.CANCELLED)
        return "should not be stored"

    run_job(str(row.pk), "tests.jobs.cancel-mid-run", 0)
    row.refresh_from_db()
    assert row.status == Job.Status.CANCELLED
    assert row.result == {}


def test_run_job_stores_an_error_for_an_unresolvable_target(user):
    row = _job_for(user, function="nonexistent.module.fn")
    run_job(str(row.pk), "nonexistent.module.fn", 0)
    row.refresh_from_db()
    assert row.status == Job.Status.FAILED
    assert "registered or importable" in row.error


# --- accepted --------------------------------------------------------------------------


def test_accepted_returns_202_with_a_location_header_and_body(user):
    row = _job_for(user)
    response = accepted(_rf.get("/"), row, prefix="/jobs")
    assert response.status_code == 202
    assert response["Location"].endswith(f"/jobs/{row.pk}")
    body = json.loads(response.content)
    assert body == {"id": str(row.pk), "status": row.status, "url": response["Location"]}


# --- JobsController ---------------------------------------------------------------------


def test_jobs_controller_retrieve_allows_the_owner(user):
    row = _job_for(user)
    client = TestClient(JobsController.as_router())
    response = client.get(f"/{row.pk}", user=user)
    assert response.status_code == 200
    assert response.json()["id"] == str(row.pk)


def test_jobs_controller_retrieve_denies_other_users(user):
    other = User.objects.create(username="bob")
    row = _job_for(user)
    client = TestClient(JobsController.as_router())
    assert client.get(f"/{row.pk}", user=other).status_code == 403


def test_jobs_controller_retrieve_allows_staff(user, staff):
    row = _job_for(user)
    client = TestClient(JobsController.as_router())
    assert client.get(f"/{row.pk}", user=staff).status_code == 200


def test_jobs_controller_retrieve_404_for_a_missing_job(user):
    client = TestClient(JobsController.as_router())
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/{missing}", user=user).status_code == 404


def test_jobs_controller_requires_authentication(user):
    row = _job_for(user)
    client = TestClient(JobsController.as_router())
    assert client.get(f"/{row.pk}").status_code == 401


def test_jobs_controller_list_shows_only_the_callers_jobs(user):
    other = User.objects.create(username="bob")
    mine = _job_for(user)
    _job_for(other)
    client = TestClient(JobsController.as_router())
    items = client.get("/", user=user).json()["items"]
    assert [item["id"] for item in items] == [str(mine.pk)]


def test_jobs_controller_cancel_marks_active_jobs_cancelled(user):
    row = _job_for(user, status=Job.Status.RUNNING)
    client = TestClient(JobsController.as_router())
    response = client.post(f"/{row.pk}/cancel", user=user)
    assert response.json()["status"] == Job.Status.CANCELLED
    row.refresh_from_db()
    assert row.status == Job.Status.CANCELLED
    assert row.finished is not None


def test_jobs_controller_cancel_is_a_noop_for_terminal_jobs(user):
    row = _job_for(user, status=Job.Status.SUCCEEDED)
    client = TestClient(JobsController.as_router())
    response = client.post(f"/{row.pk}/cancel", user=user)
    assert response.json()["status"] == Job.Status.SUCCEEDED


def test_jobs_controller_cancel_denies_other_users(user):
    other = User.objects.create(username="bob")
    row = _job_for(user)
    client = TestClient(JobsController.as_router())
    assert client.post(f"/{row.pk}/cancel", user=other).status_code == 403


# --- devx_jobs management command --------------------------------------------------------


def test_devx_jobs_prune_deletes_old_terminal_jobs(user):
    old = _job_for(user, status=Job.Status.SUCCEEDED)
    Job.objects.filter(pk=old.pk).update(created=timezone.now() - timedelta(days=40))
    keep = _job_for(user, status=Job.Status.SUCCEEDED)
    call_command("devx_jobs", "prune", "--older-than", "30")
    assert not Job.objects.filter(pk=old.pk).exists()
    assert Job.objects.filter(pk=keep.pk).exists()


def test_devx_jobs_prune_requires_older_than():
    with pytest.raises(CommandError, match="older-than"):
        call_command("devx_jobs", "prune")


def test_devx_jobs_retry_replays_a_failed_job(user):
    row = _job_for(
        user,
        function="tests.jobs.add",
        arguments={"args": [4, 5], "kwargs": {}},
        status=Job.Status.FAILED,
        error="boom",
    )
    call_command("devx_jobs", "retry", str(row.pk))
    row.refresh_from_db()
    assert row.status == Job.Status.SUCCEEDED
    assert row.result == 9


def test_devx_jobs_retry_rejects_a_non_terminal_job(user):
    row = _job_for(user, status=Job.Status.QUEUED)
    with pytest.raises(CommandError, match="failed or cancelled"):
        call_command("devx_jobs", "retry", str(row.pk))


def test_devx_jobs_retry_rejects_an_unknown_id():
    with pytest.raises(CommandError, match="No job"):
        call_command("devx_jobs", "retry", "00000000-0000-0000-0000-000000000000")
