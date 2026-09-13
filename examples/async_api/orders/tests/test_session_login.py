"""The documented browser login works through real Django middleware."""

import pytest
from django.contrib.auth.models import User
from django.test import Client


@pytest.mark.django_db(transaction=True)
def test_browser_session_and_csrf() -> None:
    User.objects.create_user(username="browser", password="test-only-password")
    client = Client(enforce_csrf_checks=True)
    assert client.get("/api/orders/").status_code in (401, 403)
    login = client.get("/accounts/login/")
    assert login.status_code == 200
    assert b"csrfmiddlewaretoken" in login.content
    assert (
        client.post(
            "/accounts/login/", {"username": "browser", "password": "test-only-password"}
        ).status_code
        == 403
    )
    token = client.cookies["csrftoken"].value
    response = client.post(
        "/accounts/login/",
        {
            "username": "browser",
            "password": "test-only-password",
            "csrfmiddlewaretoken": token,
        },
    )
    assert response.status_code == 302
    assert client.get("/api/orders/").status_code == 200
