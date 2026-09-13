import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from ninja.testing import TestClient

from blog.models import Comment, Post, Tag
from config.urls import api

pytestmark = pytest.mark.django_db


@pytest.fixture
def client() -> TestClient:
    return TestClient(api)


@pytest.fixture
def ada() -> User:
    return User.objects.create(username="ada")


@pytest.mark.django_db(transaction=True)
def test_post_lifecycle(client: TestClient, ada: User) -> None:
    tag = Tag.objects.create(name="django")
    created = client.post(
        "/v1/posts/", json={"title": "Hello", "slug": "hello", "tags": [tag.pk]}, user=ada
    )
    assert created.status_code == 201
    pk = created.json()["id"]

    page = client.get("/v1/posts/?search=hell&status=draft", user=ada).json()
    assert page["count"] == 1

    published = client.post(f"/v1/posts/{pk}/publish", user=ada, headers={"Idempotency-Key": "p1"})
    assert published.json()["status"] == "published"

    assert client.delete(f"/v1/posts/{pk}", user=ada).status_code == 204
    assert client.get(f"/v1/posts/{pk}", user=ada).status_code == 404
    assert client.post(f"/v1/posts/{pk}/restore", user=ada).status_code == 200


def test_only_authors_change_their_posts(client: TestClient, ada: User) -> None:
    post = Post.objects.create(author=ada, title="Mine", slug="mine")
    bob = User.objects.create(username="bob")
    assert client.patch(f"/v1/posts/{post.pk}", json={"title": "x"}, user=bob).status_code == 403


def test_nested_comments(client: TestClient, ada: User) -> None:
    post = Post.objects.create(author=ada, title="Post", slug="post")
    response = client.post(f"/v1/posts/{post.pk}/comments/", json={"body": "Nice"}, user=ada)
    assert (response.status_code, response.json()["post_id"]) == (201, post.pk)
    assert Comment.objects.get().post == post
    assert client.get("/v1/posts/999/comments/", user=ada).status_code == 404


def test_generated_clients_are_up_to_date() -> None:
    for fmt, path in (("typescript", "clients/blog.ts"), ("python", "clients/blog_client.py")):
        call_command(
            "devx_openapi", "config.urls.api", "--format", fmt, "--output", path, "--check"
        )
