import pytest
from django.contrib.auth.models import Group, User
from django.test import Client

from ninja_devx.contrib.apikeys.auth import create_api_key
from ninja_devx.contrib.audit.log import record
from ninja_devx.contrib.grants.models import ObjectGrant
from ninja_devx.contrib.webhooks.models import WebhookDelivery, WebhookEndpoint
from ninja_devx.contrib.webhooks.outbox import publish
from ninja_devx.contrib.webhooks.signing import generate_secret
from ninja_devx.security.object_permissions import assign_perm
from tests.testapp.models import Note

pytestmark = pytest.mark.django_db


@pytest.fixture
def root():
    user = User.objects.create_superuser("root", "root@example.com", "pw")
    client = Client()
    client.force_login(user)
    return user, client


def test_changelists_and_detail_pages_render(root):
    user, client = root
    note = Note.objects.create(owner=user, text="x")
    assign_perm("testapp.view_note", Group.objects.create(name="team"), note)
    key, _ = create_api_key(user, "ci", scopes=["*"], rate_limit="10/min")
    entry = record(None, "export", note)
    hook = WebhookEndpoint.objects.create(url="https://h.example.com/", events=["*"], secret="s")
    publish("order.created", {}, broadcast=True)
    for url in [
        "/admin/ninja_devx_grants/objectgrant/",
        f"/admin/ninja_devx_grants/objectgrant/{ObjectGrant.objects.get().pk}/change/",
        "/admin/ninja_devx_apikeys/apikey/",
        f"/admin/ninja_devx_apikeys/apikey/{key.pk}/change/",
        "/admin/ninja_devx_audit/auditentry/",
        f"/admin/ninja_devx_audit/auditentry/{entry.pk}/change/",
        "/admin/ninja_devx_webhooks/webhookendpoint/",
        f"/admin/ninja_devx_webhooks/webhookendpoint/{hook.pk}/change/",
        "/admin/ninja_devx_webhooks/webhookdelivery/",
        "/admin/ninja_devx_webhooks/outboxevent/",
    ]:
        response = client.get(url)
        assert response.status_code == 200, url
    detail = client.get(f"/admin/ninja_devx_apikeys/apikey/{key.pk}/change/").content.decode()
    assert key.hashed_secret not in detail
    assert client.get("/admin/ninja_devx_apikeys/apikey/add/").status_code == 403
    assert client.get("/admin/ninja_devx_audit/auditentry/add/").status_code == 403


def test_revoke_action(root):
    user, client = root
    key, _ = create_api_key(user, "ci")
    client.post(
        "/admin/ninja_devx_apikeys/apikey/",
        {"action": "revoke", "_selected_action": [key.pk]},
    )
    key.refresh_from_db()
    assert key.revoked_at is not None


def test_audit_entries_are_read_only(root):
    _, client = root
    entry = record(None, "export", None, metadata={"a": 1})
    client.post(f"/admin/ninja_devx_audit/auditentry/{entry.pk}/change/", {"action": "tampered"})
    entry.refresh_from_db()
    assert entry.action == "export"


def test_endpoint_creation_shows_the_secret_once_and_checks_urls(root):
    _, client = root
    data = {
        "url": "https://169.254.169.254/",
        "description": "",
        "events": '["*"]',
        "is_active": "on",
    }
    response = client.post("/admin/ninja_devx_webhooks/webhookendpoint/add/", data)
    assert response.status_code == 200  # form error
    assert not WebhookEndpoint.objects.exists()

    data["url"] = "https://hooks.example.com/"
    response = client.post("/admin/ninja_devx_webhooks/webhookendpoint/add/", data, follow=True)
    endpoint = WebhookEndpoint.objects.get()
    shown = [str(message) for message in response.context["messages"]]
    assert any(endpoint.signing_secret in message for message in shown)
    change = client.get(f"/admin/ninja_devx_webhooks/webhookendpoint/{endpoint.pk}/change/")
    assert endpoint.signing_secret not in change.content.decode()


def test_retry_action(root):
    _, client = root
    WebhookEndpoint.objects.create(
        url="https://h.example.com/", events=["*"], secret=generate_secret()
    )
    publish("order.created", {}, broadcast=True)
    delivery = WebhookDelivery.objects.get()
    WebhookDelivery.objects.update(status="failed", next_attempt_at=None)
    client.post(
        "/admin/ninja_devx_webhooks/webhookdelivery/",
        {"action": "retry", "_selected_action": [delivery.pk]},
    )
    delivery.refresh_from_db()
    assert (delivery.status, delivery.next_attempt_at is not None) == ("pending", True)
