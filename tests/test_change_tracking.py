from typing import ClassVar

import pytest
from ninja import Schema
from ninja.testing import TestClient

from ninja_devx.crud import BulkUpdateMixin, CRUDController
from ninja_devx.crud.persistence import changed_fields
from ninja_devx.layers import Conflict
from tests.testapp.models import Article, Organization, Project, Task

pytestmark = pytest.mark.django_db


class ProjectOut(Schema):
    id: int
    name: str
    status: str
    is_active: bool


class ProjectIn(Schema):
    name: str
    status: str = "live"
    is_active: bool = True


class TaskOut(Schema):
    id: int
    project_id: int
    title: str


class TaskIn(Schema):
    project: int
    title: str


class Recorder:
    calls: ClassVar[list[tuple[object, dict[str, tuple[object, object]]]]] = []


@pytest.fixture(autouse=True)
def _clear_recorder():
    Recorder.calls.clear()


class RecordingProjects(CRUDController[Project, ProjectOut, ProjectIn]):
    def on_change(self, request, instance, changes):
        Recorder.calls.append((instance.pk, dict(changes)))


class RaisingProjects(CRUDController[Project, ProjectOut, ProjectIn]):
    def on_change(self, request, instance, changes):
        raise Conflict("blocked by policy")


class BulkRecordingProjects(
    BulkUpdateMixin[Project, ProjectOut, ProjectIn], CRUDController[Project, ProjectOut, ProjectIn]
):
    def on_change(self, request, instance, changes):
        Recorder.calls.append((instance.pk, dict(changes)))


class RecordingTasks(CRUDController[Task, TaskOut, TaskIn]):
    def on_change(self, request, instance, changes):
        Recorder.calls.append((instance.pk, dict(changes)))


@pytest.fixture
def org():
    return Organization.objects.create(name="acme")


@pytest.fixture
def project(org):
    return Project.objects.create(organization=org, name="Launch", status="live")


# --- changed_fields() --------------------------------------------------------------------


def test_changed_fields_reports_old_and_new_only_for_written_and_differing_keys(project):
    changes = changed_fields(project, {"name": "Renamed", "status": "live", "missing": "x"})

    assert changes == {"name": ("Launch", "Renamed")}


def test_changed_fields_compares_foreign_keys_by_pk(org):
    globex = Organization.objects.create(name="globex")
    project = Project.objects.create(organization=org, name="Launch")

    assert changed_fields(project, {"organization": globex}) == {
        "organization": (org.pk, globex.pk)
    }
    assert changed_fields(project, {"organization": globex.pk}) == {
        "organization": (org.pk, globex.pk)
    }
    assert changed_fields(project, {"organization": org}) == {}


def test_changed_fields_ignores_many_to_many_fields(django_user_model):
    user = django_user_model.objects.create(username="ada")
    article = Article.objects.create(title="A", slug="a", author=user)

    assert changed_fields(article, {"tags": [1, 2]}) == {}


# --- on_change() ---------------------------------------------------------------------------


def test_put_calls_on_change_with_old_and_new_values(project):
    client = TestClient(RecordingProjects.as_router())
    response = client.put(
        f"/{project.pk}", json={"name": "Renamed", "status": "live", "is_active": True}
    )

    assert response.status_code == 200, response.json()
    assert Recorder.calls == [(project.pk, {"name": ("Launch", "Renamed")})]


def test_patch_calls_on_change_only_for_sent_fields(project):
    client = TestClient(RecordingProjects.as_router())
    response = client.patch(f"/{project.pk}", json={"status": "archived"})

    assert response.status_code == 200, response.json()
    assert Recorder.calls == [(project.pk, {"status": ("live", "archived")})]


def test_on_change_is_not_called_when_nothing_actually_changed(project):
    client = TestClient(RecordingProjects.as_router())
    response = client.patch(f"/{project.pk}", json={"name": "Launch"})

    assert response.status_code == 200, response.json()
    assert Recorder.calls == []


def test_default_on_change_is_a_no_op(project):
    class PlainProjects(CRUDController[Project, ProjectOut, ProjectIn]):
        pass

    client = TestClient(PlainProjects.as_router())
    response = client.patch(f"/{project.pk}", json={"name": "Renamed"})

    assert response.status_code == 200, response.json()
    assert Project.objects.get(pk=project.pk).name == "Renamed"


def test_on_change_runs_inside_the_update_transaction(project):
    client = TestClient(RaisingProjects.as_router())
    response = client.patch(f"/{project.pk}", json={"name": "Renamed"})

    assert response.status_code == 409
    assert Project.objects.get(pk=project.pk).name == "Launch"


def test_on_change_reports_foreign_keys_by_pk(org):
    original = Project.objects.create(organization=org, name="A")
    other = Project.objects.create(organization=org, name="B")
    task = Task.objects.create(project=original, title="Design")

    client = TestClient(RecordingTasks.as_router())
    response = client.patch(f"/{task.pk}", json={"project": other.pk})

    assert response.status_code == 200, response.json()
    assert Recorder.calls == [(task.pk, {"project": (original.pk, other.pk)})]


def test_bulk_update_calls_on_change_per_instance(org):
    a = Project.objects.create(organization=org, name="A", status="live")
    b = Project.objects.create(organization=org, name="B", status="paused")

    client = TestClient(BulkRecordingProjects.as_router())
    response = client.post(
        "/bulk-update", json={"pks": [a.pk, b.pk], "data": {"status": "archived"}}
    )

    assert response.status_code == 200, response.json()
    assert dict(Recorder.calls) == {
        a.pk: {"status": ("live", "archived")},
        b.pk: {"status": ("paused", "archived")},
    }
