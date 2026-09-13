"""Nothing hard-coded: routes, parameter names and soft deletion are configurable."""

import pytest
from django.contrib.auth.models import User
from ninja import NinjaAPI, Schema
from ninja.testing import TestClient

from ninja_devx import ControllerConfigError
from ninja_devx.crud import CRUDController, ReadOnlyModelController, SoftDelete, SoftDeleteMixin
from tests.testapp.models import Organization, Project, Task

pytestmark = pytest.mark.django_db


class ProjectOut(Schema):
    id: int
    name: str


class ProjectIn(Schema):
    name: str


class TaskOut(Schema):
    id: int
    title: str
    slug: str | None


class Base(CRUDController[Project, ProjectOut, ProjectIn]):
    def context_data(self, request):
        return {"organization": Organization.objects.get_or_create(name="acme")[0]}


def paths(router):
    return {
        path: sorted(m for op in view.operations for m in op.methods)
        for path, view in router.path_operations.items()
    }


# --- routes ---------------------------------------------------------------------------


def test_routes_rename_disable_and_configure_operations():
    class Configured(Base):
        routes = {
            "destroy": {"enabled": False},
            "partial_update": {"path": "/{pk}/edit", "summary": "Edit a project"},
            "list": {"deprecated": True},
        }

    router = Configured.as_router()
    assert "DELETE" not in paths(router)["/{pk}"]
    assert paths(router)["/{pk}/edit"] == ["PATCH"]
    api = NinjaAPI(urls_namespace="routes")
    api.add_router("/projects", router)
    schema = api.get_openapi_schema(path_prefix="")["paths"]
    assert schema["/projects/{pk}/edit"]["patch"]["summary"] == "Edit a project"
    assert schema["/projects/"]["get"]["deprecated"] is True


def test_unknown_route_names_fail_at_startup():
    class Typo(Base):
        routes = {"destory": {"enabled": False}}

    with pytest.raises(ControllerConfigError, match="destory"):
        Typo.as_router()


# --- parameter names -------------------------------------------------------------------


class Tasks(ReadOnlyModelController[Task, TaskOut]):
    lookup_field = "slug"
    lookup_param = "slug"
    ordering_fields = ("title",)
    ordering_param = "sort"


def test_lookup_and_ordering_parameter_names():
    project = Project.objects.create(organization=Organization.objects.create(name="o"), name="p")
    Task.objects.create(project=project, title="b", slug="second")
    Task.objects.create(project=project, title="a", slug="first")
    client = TestClient(Tasks.as_router())
    assert client.get("/first").json()["title"] == "a"
    assert [t["title"] for t in client.get("/?sort=title").json()] == ["a", "b"]
    assert [t["title"] for t in client.get("/?sort=-title").json()] == ["b", "a"]

    api = NinjaAPI(urls_namespace="params")
    api.add_router("/tasks", Tasks.as_router())
    schema = api.get_openapi_schema(path_prefix="")["paths"]
    assert "/tasks/{slug}" in schema
    assert [p["name"] for p in schema["/tasks/"]["get"]["parameters"]] == ["sort"]


# --- soft delete -----------------------------------------------------------------------


@pytest.fixture
def ada():
    return User.objects.create(username="ada")


@pytest.mark.parametrize(
    ("config", "deleted", "restored"),
    [
        (
            SoftDelete("is_active", deleted=False, active=True),
            {"is_active": False},
            {"is_active": True},
        ),
        (
            SoftDelete("status", deleted="archived", active="live"),
            {"status": "archived"},
            {"status": "live"},
        ),
        ("removed_at", {"removed_at__isnull": False}, {"removed_at": None}),
    ],
)
def test_soft_delete_fields_and_values(ada, config, deleted, restored):
    class Projects(SoftDeleteMixin[Project, ProjectOut], Base):
        soft_delete = config

    client = TestClient(Projects.as_router())
    project = client.post("/", json={"name": "x"}, user=ada).json()
    assert client.delete(f"/{project['id']}", user=ada).status_code == 204
    assert Project.objects.filter(pk=project["id"], **deleted).exists()
    assert client.get("/", user=ada).json() == []
    assert client.post(f"/{project['id']}/restore", user=ada).status_code == 200
    assert Project.objects.filter(pk=project["id"], **restored).exists()


def test_soft_delete_records_who_and_when_and_renames_restore(ada):
    class Projects(SoftDeleteMixin[Project, ProjectOut], Base):
        soft_delete = SoftDelete(
            "is_active",
            deleted=False,
            active=True,
            deleted_at="removed_at",
            deleted_by="removed_by",
        )
        routes = {"restore": {"path": "/{pk}/undelete"}}

    client = TestClient(Projects.as_router())
    project_id = client.post("/", json={"name": "x"}, user=ada).json()["id"]
    client.delete(f"/{project_id}", user=ada)
    project = Project.objects.get(pk=project_id)
    assert (project.removed_by, project.removed_at is not None) == (ada, True)
    assert client.post(f"/{project_id}/undelete", user=ada).status_code == 200
    project.refresh_from_db()
    assert (project.removed_by, project.removed_at, project.is_active) == (None, None, True)


def test_soft_delete_validates_the_extra_fields():
    class Wrong(SoftDeleteMixin[Project, ProjectOut], Base):
        soft_delete = SoftDelete("is_active", deleted=False, active=True, deleted_by="name")

    with pytest.raises(ControllerConfigError, match="nullable ForeignKey"):
        Wrong.as_router()


def test_errors_suggest_the_closest_name():
    from django.test import override_settings

    from ninja_devx.configuration.settings import get_settings

    class Typo(Base):
        search_fields = ("nmae",)

    with pytest.raises(ControllerConfigError, match="Did you mean 'name'"):
        Typo.as_router()

    class RouteTypo(Base):
        routes = {"retreive": {"enabled": False}}

    with pytest.raises(ControllerConfigError, match="Did you mean 'retrieve'"):
        RouteTypo.as_router()

    from django.core.exceptions import ImproperlyConfigured

    with (
        override_settings(NINJA_DEVX={"BULK_LIMT": 3}),
        pytest.raises(ImproperlyConfigured, match="Did you mean 'BULK_LIMIT'"),
    ):
        get_settings()
