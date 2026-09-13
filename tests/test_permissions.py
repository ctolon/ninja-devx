import dataclasses

import pytest
from django.contrib.auth.models import Permission, User
from django.http import HttpRequest
from ninja import NinjaAPI
from ninja.streaming import JSONL
from ninja.testing import TestAsyncClient, TestClient

from ninja_devx import (
    AllowAny,
    BasePermission,
    Controller,
    ControllerConfigError,
    ControllerOptions,
    DenyAll,
    HasDjangoPermission,
    IsAuthenticated,
    IsAuthenticatedOrReadOnly,
    IsOwner,
    IsStaff,
    get,
    post,
)
from ninja_devx.security.permissions import AllOf, AnyOf, Not


def header_auth(request):
    return User.objects.filter(username=request.headers.get("X-User", "")).first()


class Flag(BasePermission[object]):
    def __init__(self, allowed: bool, message: str = "flag", status_code: int = 403) -> None:
        self.allowed = allowed
        self.message = message
        self.status_code = status_code

    def has_permission(self, request: HttpRequest, /) -> bool:
        return self.allowed


def client_for(*permissions, auth=None, method=get):
    class GuardedController(Controller):
        options = ControllerOptions(auth=auth, permissions=permissions)

        @method("/")
        def index(self, request):
            return {"ok": True}

    return TestClient(GuardedController.as_router())


def test_no_permissions_allows_everything():
    assert client_for().get("/").status_code == 200


def test_denial_uses_message_and_status_code():
    response = client_for(Flag(False, "nope", 418)).get("/")
    assert response.status_code == 418
    assert response.json() == {"detail": "nope"}


def test_list_of_permissions_reports_the_first_denial():
    response = client_for(Flag(True), Flag(False, "second"), Flag(False, "third")).get("/")
    assert response.json() == {"detail": "second"}


def test_and_or_not_composition():
    assert client_for(Flag(True) & Flag(True)).get("/").status_code == 200
    assert client_for(Flag(True) & Flag(False, "b")).get("/").json() == {"detail": "b"}
    assert client_for(Flag(False) | Flag(True)).get("/").status_code == 200
    assert client_for(Flag(False, "a") | Flag(False, "b")).get("/").json() == {"detail": "b"}
    assert client_for(~Flag(True)).get("/").status_code == 403
    assert client_for(~Flag(False)).get("/").status_code == 200
    assert isinstance(Flag(True) & Flag(True), AllOf)
    assert isinstance(Flag(True) | Flag(True), AnyOf)
    assert isinstance(~Flag(True), Not)


def test_combinators_are_immutable():
    combined = Flag(True) & Flag(False)
    with pytest.raises(dataclasses.FrozenInstanceError):
        combined.permissions = ()  # type: ignore[misc]
    assert combined.has_permission(HttpRequest()) is False


def test_allow_any_and_deny_all():
    assert client_for(AllowAny()).get("/").status_code == 200
    assert client_for(DenyAll()).get("/").status_code == 403


@pytest.mark.django_db
def test_is_authenticated_requires_ninja_auth():
    User.objects.create(username="ada")
    client = client_for(IsAuthenticated())
    assert client.get("/").status_code == 401  # no auth configured: request.auth is unset

    client = client_for(IsAuthenticated(), auth=header_auth)
    assert client.get("/").status_code == 401  # Ninja's own authentication failure
    assert client.get("/", headers={"X-User": "ada"}).status_code == 200


def test_is_authenticated_or_read_only():
    client = client_for(IsAuthenticatedOrReadOnly(), method=get)
    assert client.get("/").status_code == 200
    client = client_for(IsAuthenticatedOrReadOnly(), method=post)
    assert client.post("/").status_code == 401


@pytest.mark.django_db
def test_user_based_permissions():
    User.objects.create(username="ada", is_staff=True)
    bob = User.objects.create(username="bob")
    bob.user_permissions.add(Permission.objects.get(codename="view_user"))

    staff = client_for(IsStaff(), auth=header_auth)
    assert staff.get("/", headers={"X-User": "ada"}).status_code == 200
    assert staff.get("/", headers={"X-User": "bob"}).status_code == 403

    perms = client_for(HasDjangoPermission("auth.view_user"), auth=header_auth)
    assert perms.get("/", headers={"X-User": "bob"}).status_code == 200
    assert perms.get("/", headers={"X-User": "ada"}).status_code == 403


def test_operation_permissions_replace_controller_permissions():
    class MixedController(Controller):
        options = ControllerOptions(permissions=[DenyAll()])

        @get("/private")
        def private(self, request): ...

        @get("/public", permissions=[])
        def public(self, request):
            return {"ok": True}

    client = TestClient(MixedController.as_router())
    assert client.get("/private").status_code == 403
    assert client.get("/public").status_code == 200


def test_permissions_run_before_the_controller_is_created():
    created = []

    class LazyController(Controller):
        options = ControllerOptions(permissions=[DenyAll()])

        def __init__(self) -> None:
            created.append(self)

        @get("/")
        def index(self, request): ...

    TestClient(LazyController.as_router()).get("/")
    assert created == []


@dataclasses.dataclass
class Document:
    owner_id: int


class DocumentController(Controller):
    options = ControllerOptions(auth=header_auth, permissions=[IsAuthenticated() & IsOwner()])

    @get("/{owner_id}")
    def retrieve(self, request, owner_id: int):
        document = Document(owner_id=owner_id)
        self.check_object_permissions(request, document)
        return {"owner": document.owner_id}


@pytest.mark.django_db
def test_object_permissions():
    ada = User.objects.create(username="ada")
    client = TestClient(DocumentController.as_router())
    assert client.get(f"/{ada.pk}", headers={"X-User": "ada"}).status_code == 200
    assert client.get(f"/{ada.pk + 1}", headers={"X-User": "ada"}).status_code == 403


class AsyncFlag(BasePermission[object]):
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed

    async def has_permission(self, request: HttpRequest, /) -> bool:
        return self.allowed


async def test_async_permissions_guard_async_operations():
    class AsyncController(Controller):
        @get("/allowed", permissions=[AsyncFlag(True) & Flag(True)])
        async def allowed(self, request):
            return {"ok": True}

        @get("/denied", permissions=[Flag(True), AsyncFlag(False) | Flag(False, "no")])
        async def denied(self, request): ...

    client = TestAsyncClient(AsyncController.as_router())
    assert (await client.get("/allowed")).status_code == 200
    denied = await client.get("/denied")
    assert (denied.status_code, denied.json()) == (403, {"detail": "no"})


def test_async_permissions_cannot_guard_sync_operations():
    class MismatchedController(Controller):
        @get("/", permissions=[AsyncFlag(True)])
        def index(self, request): ...

    with pytest.raises(ControllerConfigError, match="async permissions"):
        MismatchedController.as_router()


async def test_async_streams_check_permissions_before_the_first_item():
    class StreamController(Controller):
        @get("/denied", response=JSONL[int], permissions=[DenyAll()])
        async def denied(self, request):
            yield 1

        @get("/allowed", response=JSONL[int], permissions=[AllowAny()])
        async def allowed(self, request):
            yield 1

    client = TestAsyncClient(StreamController.as_router())
    denied = await client.get("/denied")
    assert denied.status_code == 403
    assert not denied.streaming
    assert (await client.get("/allowed")).content == b"1\n"


def test_sync_streaming_denial_is_a_regular_response():
    class StreamController(Controller):
        @get("/", response=JSONL[int], permissions=[DenyAll()])
        def stream(self, request):
            yield 1

    response = TestClient(StreamController.as_router()).get("/")
    assert response.status_code == 403
    assert not response.streaming


def test_permissions_appear_nowhere_in_openapi():
    api = NinjaAPI()
    api.add_router("", DocumentController.as_router())
    operation = api.get_openapi_schema(path_prefix="")["paths"]["/{owner_id}"]["get"]
    assert [p["name"] for p in operation["parameters"]] == ["owner_id"]
