from datetime import date

import django_filters
import pytest
from django.contrib.auth.models import User
from ninja import NinjaAPI
from ninja.testing import TestClient

from ninja_devx import ControllerConfigError
from ninja_devx.crud import ReadOnlyModelController
from tests.testapp.api import ArticleOut
from tests.testapp.models import Article, Note

pytestmark = pytest.mark.django_db


class ArticleFilter(django_filters.FilterSet):
    created = django_filters.DateFromToRangeFilter()
    mine = django_filters.BooleanFilter(method="filter_mine", help_text="Only my articles")
    slug = django_filters.MultipleChoiceFilter(choices=[("apple", "Apple"), ("pear", "Pear")])

    class Meta:
        model = Article
        fields = {"title": ["icontains"], "published": ["exact"], "id": ["in"]}

    def filter_mine(self, queryset, name, value):
        return queryset.filter(author=self.request.user) if value else queryset


class Articles(ReadOnlyModelController[Article, ArticleOut]):
    filterset_class = ArticleFilter
    search_fields = ("body",)


@pytest.fixture
def articles():
    ada, bob = User.objects.create(username="ada"), User.objects.create(username="bob")
    Article.objects.create(title="Apple pie", slug="apple", author=ada, published=True)
    Article.objects.create(title="Pear tart", slug="pear", author=bob, body="apple sauce")
    return ada


def titles(response):
    assert response.status_code == 200, response.json()
    return [row["title"] for row in response.json()]


def test_filters_run_through_the_filterset_with_the_request(articles):
    client = TestClient(Articles.as_router())
    assert titles(client.get("/?title__icontains=PIE")) == ["Apple pie"]
    assert titles(client.get("/?published=false")) == ["Pear tart"]
    assert titles(client.get("/?slug=apple&slug=pear")) == ["Apple pie", "Pear tart"]
    assert titles(client.get(f"/?created_after={date.today()}")) == ["Apple pie", "Pear tart"]
    assert titles(client.get("/?created_before=2000-01-01")) == []
    assert titles(client.get("/?mine=true", user=articles)) == ["Apple pie"]
    ids = Article.objects.order_by("id").values_list("id", flat=True)
    assert titles(client.get(f"/?id__in={ids[1]}")) == ["Pear tart"]
    assert titles(client.get("/?search=sauce")) == ["Pear tart"]


def test_rejected_values_are_query_validation_errors(articles):
    response = TestClient(Articles.as_router()).get("/?slug=plum")
    assert response.status_code == 422
    [error] = response.json()["detail"]
    assert (error["type"], error["loc"]) == ("invalid_choice", ["query", "slug"])


def test_filters_are_typed_query_parameters():
    api = NinjaAPI(urls_namespace="filtersets")
    api.add_router("/articles", Articles.as_router())
    parameters = {
        parameter["name"]: parameter
        for parameter in api.get_openapi_schema(path_prefix="")["paths"]["/articles/"]["get"][
            "parameters"
        ]
    }
    assert {"created_after", "created_before", "mine", "slug", "search"} <= set(parameters)
    assert parameters["mine"]["description"] == "Only my articles"
    assert parameters["created_after"]["schema"]["anyOf"][0]["format"] == "date"
    assert parameters["slug"]["schema"]["anyOf"][0]["type"] == "array"


def test_a_filterset_for_another_model_fails_at_startup():
    class Notes(ReadOnlyModelController[Note, ArticleOut]):
        filterset_class = ArticleFilter

    with pytest.raises(ControllerConfigError, match="filters Article, not Note"):
        Notes.as_router()


def test_filterset_and_filter_fields_are_exclusive():
    class Both(ReadOnlyModelController[Article, ArticleOut]):
        filterset_class = ArticleFilter
        filter_fields = {"published": ("exact",)}

    with pytest.raises(ControllerConfigError, match="filterset_class or filter_fields"):
        Both.as_router()
