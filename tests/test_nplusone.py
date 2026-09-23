from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.http import HttpRequest
from ninja import NinjaAPI, Schema
from ninja.testing import TestClient

from ninja_devx import Controller, get
from ninja_devx.contrib.nplusone import (
    NPlusOneMiddleware,
    NPlusOnePlugin,
    explain_n_plus_one,
    zeal_installed,
)
from ninja_devx.crud import ReadOnlyModelController
from ninja_devx.plugins import install
from ninja_devx.testing.clients import client_for
from tests.testapp.models import Article

pytestmark = pytest.mark.django_db

requires_zeal = pytest.mark.skipif(
    not zeal_installed(), reason="requires django-zeal (pip install ninja-devx[zeal])"
)
requires_no_zeal = pytest.mark.skipif(
    zeal_installed(), reason="exercises the no-op path taken when zeal is not installed"
)


class ArticleAuthorOut(Schema):
    id: int
    author_username: str

    @staticmethod
    def resolve_author_username(obj: Article) -> str:
        return obj.author.username


class UnoptimizedArticles(ReadOnlyModelController[Article, ArticleAuthorOut]):
    optimize_queries = False


class LoopingArticles(Controller):
    """Reads a to-one relation in a plain loop, so the N+1 error is raised directly
    by application code rather than wrapped by pydantic during response validation."""

    @get("/")
    def list_articles(self, request: HttpRequest) -> list[str]:
        return [article.author.username for article in Article.objects.all()]


def _make_articles(count: int) -> None:
    for index in range(count):
        user = User.objects.create(username=f"user-{index}")
        Article.objects.create(title=f"T{index}", slug=f"t{index}", author=user)


def test_explain_n_plus_one_names_controller_and_fix():
    message = "N+1 detected on tests.Article.author at api.py:12 in list"
    explained = explain_n_plus_one(message, controller="ArticleController.list")
    assert message in explained
    assert "in ArticleController.list" in explained
    assert "related = ('author',)" in explained
    assert "@requires_related('author')" in explained
    assert "ninja_devx.crud.optimization" in explained


def test_explain_n_plus_one_without_controller_omits_in_clause():
    explained = explain_n_plus_one("N+1 detected on tests.Article.author at api.py:12 in list")
    assert "Fix: add" in explained


def test_explain_n_plus_one_passes_through_foreign_messages():
    assert explain_n_plus_one("some other error") == "some other error"


@requires_no_zeal
def test_middleware_is_a_noop_without_zeal():
    _make_articles(1)
    client = client_for(UnoptimizedArticles, middleware=[NPlusOneMiddleware()])
    response = client.get("/")
    assert response.status_code == 200


@requires_zeal
def test_plugin_raises_a_rewritten_n_plus_one_error():
    api = NinjaAPI()
    install(api, [NPlusOnePlugin()])
    api.add_router("/articles", LoopingArticles.as_router())
    _make_articles(2)
    client = TestClient(api)
    with pytest.raises(Exception, match="N\\+1 detected") as exc_info:
        client.get("/articles/")
    message = str(exc_info.value)
    assert "author" in message
    assert "LoopingArticles" in message


@requires_zeal
def test_middleware_alone_also_wraps_the_request():
    _make_articles(2)
    client = client_for(UnoptimizedArticles, middleware=[NPlusOneMiddleware()])
    with pytest.raises(Exception, match="N\\+1 detected"):
        client.get("/")


@requires_zeal
def test_strict_queries_fixture_raises_on_n_plus_one(strict_queries):
    _make_articles(2)
    client = client_for(UnoptimizedArticles)
    with pytest.raises(Exception, match="N\\+1 detected"):
        client.get("/")


@requires_no_zeal
def test_strict_queries_skips_without_zeal(pytester, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parent.parent))
    pytester.makepyfile(
        """
        def test_it(strict_queries):
            pass
        """
    )
    result = pytester.runpytest_subprocess("--ds=tests.settings", "-p", "no:cacheprovider")
    result.assert_outcomes(skipped=1)
