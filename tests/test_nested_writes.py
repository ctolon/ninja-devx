import pytest
from ninja import Schema
from ninja.testing import TestAsyncClient, TestClient

from ninja_devx import ControllerConfigError
from ninja_devx.crud import CRUDController
from ninja_devx.crud.nested_writes import Nested, NestedWritesMixin
from tests.testapp.models import Organization, Project, Task

pytestmark = pytest.mark.django_db


class TaskIn(Schema):
    id: int | None = None
    title: str
    slug: str | None = None


class ProjectIn(Schema):
    organization: int
    name: str
    is_active: bool = True
    tasks: list[TaskIn] = []


class ProjectOut(Schema):
    id: int
    name: str
    is_active: bool


class Projects(
    NestedWritesMixin[Project, ProjectIn], CRUDController[Project, ProjectOut, ProjectIn]
):
    nested = {"tasks": Nested(Task, "project", TaskIn)}


@pytest.fixture
def org():
    return Organization.objects.create(name="acme")


@pytest.fixture
def client():
    return TestClient(Projects.as_router())


def task_titles(project):
    return list(Task.objects.filter(project=project).order_by("id").values_list("title", flat=True))


def test_create_writes_parent_and_children_in_one_transaction(client, org):
    response = client.post(
        "/",
        json={
            "organization": org.pk,
            "name": "Launch",
            "tasks": [{"title": "Design"}, {"title": "Build"}],
        },
    )

    assert response.status_code == 201, response.json()
    project = Project.objects.get(name="Launch")
    assert task_titles(project) == ["Design", "Build"]
    assert set(Task.objects.filter(project=project).values_list("project_id", flat=True)) == {
        project.pk
    }


def test_create_child_error_rolls_back_everything_and_reports_the_path(client, org):
    other = Project.objects.create(organization=org, name="Other")
    Task.objects.create(project=other, title="Existing", slug="dup")

    response = client.post(
        "/",
        json={
            "organization": org.pk,
            "name": "Launch",
            "tasks": [{"title": "Design", "slug": "ok"}, {"title": "Build", "slug": "dup"}],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "tasks", 1, "slug"]
    assert not Project.objects.filter(name="Launch").exists()
    assert not Task.objects.filter(title__in=["Design", "Build"]).exists()


def test_put_matches_by_key_creates_and_deletes_missing_children(client, org):
    created = client.post(
        "/",
        json={
            "organization": org.pk,
            "name": "Launch",
            "tasks": [{"title": "Design"}, {"title": "Build"}],
        },
    ).json()
    project = Project.objects.get(pk=created["id"])
    keep, drop = Task.objects.filter(project=project).order_by("id")

    response = client.put(
        f"/{project.pk}",
        json={
            "organization": org.pk,
            "name": "Launch",
            "is_active": True,
            "tasks": [{"id": keep.pk, "title": "Design v2"}, {"title": "Ship"}],
        },
    )

    assert response.status_code == 200, response.json()
    assert task_titles(project) == ["Design v2", "Ship"]
    assert not Task.objects.filter(pk=drop.pk).exists()
    assert Task.objects.get(pk=keep.pk).title == "Design v2"


def test_put_omitting_the_nested_field_uses_its_default_and_clears_children(client, org):
    created = client.post(
        "/", json={"organization": org.pk, "name": "Launch", "tasks": [{"title": "Design"}]}
    ).json()
    project = Project.objects.get(pk=created["id"])

    response = client.put(
        f"/{project.pk}", json={"organization": org.pk, "name": "Renamed", "is_active": False}
    )

    assert response.status_code == 200, response.json()
    assert Task.objects.filter(project=project).count() == 0


def test_remove_missing_false_keeps_children_not_listed(org):
    class KeepChildren(Projects):
        nested = {"tasks": Nested(Task, "project", TaskIn, remove_missing=False)}

    keep_client = TestClient(KeepChildren.as_router())
    created = keep_client.post(
        "/",
        json={
            "organization": org.pk,
            "name": "Launch",
            "tasks": [{"title": "Design"}, {"title": "Build"}],
        },
    ).json()
    project = Project.objects.get(pk=created["id"])

    response = keep_client.put(
        f"/{project.pk}",
        json={
            "organization": org.pk,
            "name": "Launch",
            "is_active": True,
            "tasks": [{"title": "Extra"}],
        },
    )

    assert response.status_code == 200, response.json()
    assert Task.objects.filter(project=project).count() == 3


def test_partial_update_without_the_nested_field_leaves_children_untouched(client, org):
    created = client.post(
        "/", json={"organization": org.pk, "name": "Launch", "tasks": [{"title": "Design"}]}
    ).json()
    project = Project.objects.get(pk=created["id"])

    response = client.patch(f"/{project.pk}", json={"name": "Renamed"})

    assert response.status_code == 200, response.json()
    assert task_titles(project) == ["Design"]
    assert Project.objects.get(pk=project.pk).name == "Renamed"


def test_partial_update_with_the_nested_field_applies_the_same_semantics(client, org):
    created = client.post(
        "/", json={"organization": org.pk, "name": "Launch", "tasks": [{"title": "Design"}]}
    ).json()
    project = Project.objects.get(pk=created["id"])
    existing = Task.objects.get(project=project)

    response = client.patch(
        f"/{project.pk}",
        json={"tasks": [{"id": existing.pk, "title": "Design v2"}, {"title": "New"}]},
    )

    assert response.status_code == 200, response.json()
    assert sorted(task_titles(project)) == ["Design v2", "New"]


def test_nested_field_must_be_declared_on_the_input_schema():
    class BareIn(Schema):
        organization: int
        name: str

    class Bare(NestedWritesMixin[Project, BareIn], CRUDController[Project, ProjectOut, BareIn]):
        nested = {"tasks": Nested(Task, "project", TaskIn)}

    with pytest.raises(ControllerConfigError, match="tasks"):
        Bare.as_router()


def test_nested_foreign_key_must_point_to_the_parent_model():
    class Wrong(
        NestedWritesMixin[Project, ProjectIn], CRUDController[Project, ProjectOut, ProjectIn]
    ):
        nested = {"tasks": Nested(Task, "title", TaskIn)}

    with pytest.raises(ControllerConfigError, match="title"):
        Wrong.as_router()


@pytest.mark.django_db(transaction=True)
async def test_async_controller_writes_children_too():
    org = await Organization.objects.acreate(name="acme")

    class AsyncProjects(Projects):
        mode = "async"

    client = TestAsyncClient(AsyncProjects.as_router())
    response = await client.post(
        "/",
        json={
            "organization": org.pk,
            "name": "Launch",
            "tasks": [{"title": "Design"}, {"title": "Build"}],
        },
    )

    assert response.status_code == 201, response.json()
    project_id = response.json()["id"]
    assert await Task.objects.filter(project_id=project_id).acount() == 2


@pytest.mark.django_db(transaction=True)
async def test_async_controller_syncs_children_on_update():
    org = await Organization.objects.acreate(name="acme")

    class AsyncProjects(Projects):
        mode = "async"

    client = TestAsyncClient(AsyncProjects.as_router())
    created = (
        await client.post(
            "/", json={"organization": org.pk, "name": "Launch", "tasks": [{"title": "Design"}]}
        )
    ).json()
    project_id = created["id"]

    response = await client.patch(f"/{project_id}", json={"tasks": [{"title": "New"}]})

    assert response.status_code == 200, response.json()
    titles = sorted(
        [task.title async for task in Task.objects.filter(project_id=project_id).order_by("id")]
    )
    assert titles == ["New"]
