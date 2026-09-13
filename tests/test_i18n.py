import importlib.util
import sys
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.utils import translation
from ninja.testing import TestClient

from ninja_devx._internal.i18n import not_found
from ninja_devx.layers.errors import NotFound
from tests.testapp.api import ArticleController
from tests.testapp.models import Article

pytestmark = pytest.mark.django_db
ROOT = Path(__file__).resolve().parent.parent


def test_catalogs_are_complete_and_compiled():
    if not (ROOT / "tools" / "messages.py").exists():
        pytest.skip("needs the source tree")
    spec = importlib.util.spec_from_file_location("messages_tool", ROOT / "tools" / "messages.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["messages_tool"] = module
    spec.loader.exec_module(module)
    assert module.main(["--check"]) == 0, "run `uv run python tools/messages.py`"


def test_messages_fall_back_to_english_without_a_catalog():
    client = TestClient(ArticleController.as_router())
    User.objects.create(username="ada")
    bob = User.objects.create(username="bob")
    article = Article.objects.create(title="t", slug="t", author=bob)
    with translation.override("de"):
        denied = client.delete(f"/{article.pk}", headers={"X-User": "ada"})
        assert denied.json()["detail"] == "You do not have permission to perform this action."
        assert str(not_found(Article)) == "Article not found"
        assert str(NotFound()) == "Not found."
    assert str(NotFound()) == "Not found."


def test_custom_messages_pass_through():
    assert str(NotFound("Order 7 is gone")) == "Order 7 is gone"
