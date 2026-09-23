import pytest
import rules
from django.contrib.auth.models import User
from django.http import HttpRequest
from ninja.testing import TestAsyncClient, TestClient

from ninja_devx import Also, ControllerOptions, IsAuthenticated, get
from ninja_devx.contrib.rules import HasRule
from ninja_devx.crud import ReadOnlyModelController
from tests.testapp.api import ArticleOut
from tests.testapp.models import Article

pytestmark = pytest.mark.django_db


@rules.predicate
def is_author(user, article):
    return article is None or article.author_id == user.pk


rules.add_perm("testapp.change_article_rule", is_author | rules.is_superuser)


class Articles(ReadOnlyModelController[Article, ArticleOut]):
    options = ControllerOptions(
        permissions=[IsAuthenticated(), HasRule("testapp.change_article_rule")]
    )

    @get("/{pk}/staff-note", permissions=Also(HasRule(lambda user: user.is_staff)))
    def staff_note(self, request, pk: int):
        return {"note": self.get_object(request, pk).title}


@pytest.fixture
def authors():
    ada, bob = User.objects.create(username="ada"), User.objects.create(username="bob")
    article = Article.objects.create(title="Ada's", slug="adas", author=ada)
    return ada, bob, article


def test_permission_rules_are_checked_against_the_object(authors):
    ada, bob, article = authors
    client = TestClient(Articles.as_router())
    assert client.get(f"/{article.pk}", user=ada).status_code == 200
    assert client.get(f"/{article.pk}", user=bob).status_code == 403
    assert client.get("/", user=bob).status_code == 200  # list: no object, rule sees None


def test_plain_callables_become_predicates(authors):
    ada, _, article = authors
    client = TestClient(Articles.as_router())
    assert client.get(f"/{article.pk}/staff-note", user=ada).status_code == 403
    ada.is_staff = True
    ada.save()
    assert client.get(f"/{article.pk}/staff-note", user=ada).json() == {"note": "Ada's"}


@pytest.mark.django_db(transaction=True)
async def test_async_operations_run_rules_in_a_thread(authors):
    ada, bob, article = authors

    class AsyncArticles(Articles):
        mode = "async"

    client = TestAsyncClient(AsyncArticles.as_router())
    assert (await client.get(f"/{article.pk}", user=ada)).status_code == 200
    assert (await client.get(f"/{article.pk}", user=bob)).status_code == 403


def test_anonymous_callers_are_tested_as_anonymous_users():
    assert HasRule(rules.is_authenticated).has_permission(HttpRequest()) is False
    assert HasRule(~rules.is_authenticated).has_permission(HttpRequest()) is True
