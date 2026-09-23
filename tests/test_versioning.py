from ninja import Schema
from ninja.testing import TestAsyncClient, TestClient

from ninja_devx import Controller, get
from ninja_devx.http.versioning import VersionedResponseMiddleware, VersionedResponseMixin

_POSTS = {
    1: {"id": 1, "title": "Hello", "summary": "Hello world"},
    2: {"id": 2, "title": "Bye", "summary": "Bye world"},
}


class PostOut(Schema):
    id: int
    title: str
    summary: str


class PostOutV1(Schema):
    id: int
    title: str


class Posts(VersionedResponseMixin, Controller):
    response_versions = {1: PostOutV1}

    @get("/{pk}", response=PostOut)
    def retrieve(self, request, pk: int) -> dict:
        return _POSTS[pk]

    @get("/", response=list[PostOut])
    def list(self, request):
        return list(_POSTS.values())


class AsyncPosts(VersionedResponseMixin, Controller):
    response_versions = {1: PostOutV1}

    @get("/{pk}", response=PostOut)
    async def retrieve(self, request, pk: int) -> dict:
        return _POSTS[pk]


def test_default_version_is_latest_and_documents_x_api_version_header():
    client = TestClient(Posts.as_router())
    response = client.get("/1")
    assert response.status_code == 200
    assert response.json() == _POSTS[1]
    assert response["X-API-Version"] == "2"


def test_accept_version_downgrades_a_single_object():
    client = TestClient(Posts.as_router())
    response = client.get("/1", headers={"Accept-Version": "1"})
    assert response.status_code == 200
    assert response.json() == {"id": 1, "title": "Hello"}
    assert response["X-API-Version"] == "1"


def test_accept_version_downgrades_a_list():
    client = TestClient(Posts.as_router())
    response = client.get("/", headers={"Accept-Version": "1"})
    assert response.status_code == 200
    assert response.json() == [{"id": 1, "title": "Hello"}, {"id": 2, "title": "Bye"}]
    assert response["X-API-Version"] == "1"


def test_unknown_version_is_rejected_with_406_in_the_package_error_format():
    client = TestClient(Posts.as_router())
    response = client.get("/1", headers={"Accept-Version": "99"})
    assert response.status_code == 406
    body = response.json()
    assert body["code"] == "unsupported_api_version"
    assert "99" in body["detail"]


def test_non_integer_version_is_rejected_with_406():
    client = TestClient(Posts.as_router())
    response = client.get("/1", headers={"Accept-Version": "not-a-number"})
    assert response.status_code == 406


def test_openapi_documents_the_header_and_406():
    from ninja import NinjaAPI

    api = NinjaAPI()
    api.add_router("/posts", Posts.as_router())
    schema = api.get_openapi_schema(path_prefix="")
    operation = schema["paths"]["/posts/{pk}"]["get"]
    headers = [p["name"] for p in operation["parameters"] if p["in"] == "header"]
    assert "Accept-Version" in headers
    assert 406 in operation["responses"]


async def test_async_operation_is_versioned_too():
    client = TestAsyncClient(AsyncPosts.as_router())
    response = await client.get("/1", headers={"Accept-Version": "1"})
    assert response.status_code == 200
    assert response.json() == {"id": 1, "title": "Hello"}
    assert response["X-API-Version"] == "1"


def test_middleware_can_be_used_directly_without_the_mixin():
    middleware = VersionedResponseMiddleware({1: PostOutV1})
    assert middleware.current == 2
