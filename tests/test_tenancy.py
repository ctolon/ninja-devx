import pytest
from django.contrib.auth.models import User
from django.test import override_settings
from ninja import Schema
from ninja.testing import TestAsyncClient, TestClient

from ninja_devx import Container, ControllerConfigError, current_tenant, request_context
from ninja_devx.crud import CRUDController, Parent
from ninja_devx.layers import RequestContext
from tests.testapp.models import Organization, Project, Task

pytestmark = pytest.mark.django_db


class ProjectOut(Schema):
    id: int
    name: str
    organization_id: int


class ProjectIn(Schema):
    name: str


class TaskOut(Schema):
    id: int
    title: str


class TaskIn(Schema):
    title: str


class Projects(CRUDController[Project, ProjectOut, ProjectIn]):
    tenant_field = "organization"


def org_from_header(request):
    header = request.headers.get("X-Org")
    return Organization.objects.filter(pk=header).first() if header else None


class HeaderProjects(Projects):
    tenant_resolver = org_from_header


class Tasks(CRUDController[Task, TaskOut, TaskIn]):
    tenant_field = "project__organization"
    parent = Parent(Project, field="project", tenant_field="organization")


@pytest.fixture
def orgs():
    acme, globex = (
        Organization.objects.create(name="acme"),
        Organization.objects.create(name="globex"),
    )
    return acme, globex


def test_queries_are_scoped_and_create_sets_the_tenant(orgs):
    acme, globex = orgs
    theirs = Project.objects.create(organization=globex, name="secret")
    client = TestClient(Projects.as_router())
    created = client.post("/", json={"name": "rocket"}, tenant=acme)
    assert (created.status_code, created.json()["organization_id"]) == (201, acme.pk)
    assert [p["name"] for p in client.get("/", tenant=acme).json()] == ["rocket"]
    assert client.get(f"/{theirs.pk}", tenant=acme).status_code == 404
    assert client.delete(f"/{theirs.pk}", tenant=acme).status_code == 404
    assert Project.objects.filter(pk=theirs.pk).exists()


def test_no_tenant_is_forbidden(orgs):
    response = TestClient(Projects.as_router()).get("/")
    assert (response.status_code, response.json()["code"]) == (403, "tenant_required")


def test_resolver_from_the_controller(orgs):
    acme, _ = orgs
    Project.objects.create(organization=acme, name="a")
    client = TestClient(HeaderProjects.as_router())
    assert len(client.get("/", headers={"X-Org": str(acme.pk)}).json()) == 1
    assert client.get("/", headers={"X-Org": "999"}).status_code == 403


def test_resolver_from_settings_and_request_context(orgs):
    acme, globex = orgs
    Project.objects.create(organization=globex, name="g")
    with override_settings(NINJA_DEVX={"TENANT_RESOLVER": "tests.test_tenancy.org_from_header"}):
        client = TestClient(Projects.as_router())
        assert len(client.get("/", headers={"X-Org": str(globex.pk)}).json()) == 1

    context_key = RequestContext[User, Organization]
    container = Container()
    container.scoped(context_key, request_context(User, tenant=lambda request, user: acme))

    class ContextProjects(Projects):
        tenant_context = context_key

    ada = User.objects.create(username="ada")
    client = TestClient(ContextProjects.as_router(container=container))
    assert client.get("/", user=ada).json() == []


def test_input_schema_must_not_accept_the_tenant():
    class WithOrg(ProjectIn):
        organization: int

    class Leaky(CRUDController[Project, ProjectOut, WithOrg]):
        tenant_field = "organization"

    with pytest.raises(ControllerConfigError, match="must not accept"):
        Leaky.as_router()


def test_related_tenant_needs_a_tenant_scoped_parent():
    class Unscoped(CRUDController[Task, TaskOut, TaskIn]):
        tenant_field = "project__organization"

    with pytest.raises(ControllerConfigError, match="follows a relation"):
        Unscoped.as_router()


def test_nested_resources_scope_the_parent_by_tenant(orgs):
    acme, globex = orgs
    mine = Project.objects.create(organization=acme, name="mine")
    theirs = Project.objects.create(organization=globex, name="theirs")
    from ninja import NinjaAPI

    api = NinjaAPI(urls_namespace="tenant-tasks")
    api.add_router("/projects/{project_pk}/tasks", Tasks.as_router())
    client = TestClient(api)
    assert (
        client.post(f"/projects/{mine.pk}/tasks/", json={"title": "t"}, tenant=acme).status_code
        == 201
    )
    assert (
        client.post(f"/projects/{theirs.pk}/tasks/", json={"title": "t"}, tenant=acme).status_code
        == 404
    )
    assert Task.objects.count() == 1


@pytest.mark.django_db(transaction=True)
async def test_async_tenancy_with_an_async_resolver():
    acme = await Organization.objects.acreate(name="acme")
    await Project.objects.acreate(organization=acme, name="a")

    async def resolver(request):
        return acme

    class AsyncProjects(Projects):
        mode = "async"
        tenant_resolver = resolver

    client = TestAsyncClient(AsyncProjects.as_router())
    assert [p["name"] for p in (await client.get("/")).json()] == ["a"]
    created = await client.post("/", json={"name": "b"})
    assert created.json()["organization_id"] == acme.pk


def test_current_tenant_is_cached_on_the_request(orgs, rf):
    acme, _ = orgs
    request = rf.get("/")
    assert current_tenant(request) is None
    request.tenant = acme
    Projects().get_tenant(request)
    assert current_tenant(request) == acme
