import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from ninja.testing import TestClient

from config.urls import api
from tracker.models import Membership, Workspace


@pytest.fixture(autouse=True)
def fresh_throttles() -> None:
    cache.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(api)


@pytest.fixture
def acme(db: None) -> Workspace:
    workspace = Workspace.objects.create(slug="acme", name="Acme")
    admin = User.objects.create(username="alice")
    member = User.objects.create(username="bob")
    Membership.objects.create(workspace=workspace, user=admin, role=Membership.Role.ADMIN)
    Membership.objects.create(workspace=workspace, user=member)
    return workspace


def as_user(username: str, workspace: str = "acme") -> dict[str, str]:
    return {"Authorization": f"Bearer {username}", "X-Workspace": workspace}
