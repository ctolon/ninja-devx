"""OpenAPI snapshot of the test API; accept changes with ``pytest --update-snapshots``."""

from ninja import NinjaAPI

from ninja_devx import mount
from tests.testapp.api import ArticleController, PingController, SlugArticleController


def test_openapi_snapshot(openapi_snapshot):
    api = NinjaAPI(title="Snapshot", version="1")
    mount(
        api,
        {
            "/articles": ArticleController,
            "/by-slug": SlugArticleController,
            "/ping": PingController,
        },
    )
    openapi_snapshot(api)
