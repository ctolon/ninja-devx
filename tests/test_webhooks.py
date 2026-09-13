import json
import socket
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import transaction
from django.utils import timezone
from ninja.testing import TestClient

from ninja_devx.contrib.webhooks.api import WebhookEndpointController
from ninja_devx.contrib.webhooks.models import OutboxEvent, WebhookDelivery, WebhookEndpoint
from ninja_devx.contrib.webhooks.network import SafeHTTPTransport, UnsafeURL, URLPolicy, check_url
from ninja_devx.contrib.webhooks.outbox import deliver_due, event_matches, publish
from ninja_devx.contrib.webhooks.signing import (
    InvalidSignature,
    generate_secret,
    signature_headers,
    verify_signature,
)

pytestmark = pytest.mark.django_db


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, body, headers, timeout):
        self.calls.append((url, body, headers))
        result = self.responses.pop(0) if self.responses else 200
        if isinstance(result, Exception):
            raise result
        return result


def endpoint(**fields):
    defaults = {
        "url": "https://example.com/hook",
        "events": ["order.*"],
        "secret": generate_secret(),
    }
    return WebhookEndpoint.objects.create(**{**defaults, **fields})


def test_signatures_round_trip_and_reject_tampering():
    secret = generate_secret()
    body = b'{"a":1}'
    headers = signature_headers(secret, "msg_1", body, timestamp=1_700_000_000)
    verify_signature(secret, headers, body, now=1_700_000_010)
    verify_signature([generate_secret(), secret], headers, body, now=1_700_000_010)  # rotation
    with pytest.raises(InvalidSignature):
        verify_signature(secret, headers, b'{"a":2}', now=1_700_000_010)
    with pytest.raises(InvalidSignature, match="tolerance"):
        verify_signature(secret, headers, body, now=1_700_001_000)
    with pytest.raises(InvalidSignature, match="missing"):
        verify_signature(secret, {}, body)


def test_known_standard_webhooks_vector():
    # From the Standard Webhooks reference implementation.
    secret = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"
    body = b'{"test": 2432232314}'
    headers = {
        "webhook-id": "msg_p5jXN8AQM9LWM0D4loKWxJek",
        "webhook-timestamp": "1614265330",
        "webhook-signature": "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE=",
    }
    verify_signature(secret, headers, body, now=1614265330)


def test_event_patterns():
    assert event_matches(["*"], "order.created")
    assert event_matches(["order.*"], "order.created")
    assert not event_matches(["order.*"], "orders.created")
    assert not event_matches(["invoice.paid"], "order.created")


def test_publish_is_transactional_and_matches_endpoints():
    orders = endpoint()
    endpoint(events=["invoice.*"])
    endpoint(events=["*"], is_active=False)

    def rolled_back():
        with transaction.atomic():
            publish("order.created", {"id": 1}, broadcast=True)
            raise RuntimeError

    with pytest.raises(RuntimeError):
        rolled_back()
    assert not OutboxEvent.objects.exists()
    event = publish("order.created", {"id": 1, "at": timezone.now()}, broadcast=True)
    assert [d.endpoint for d in event.deliveries.all()] == [orders]


def test_delivery_success_and_signed_body():
    hook = endpoint()
    event = publish("order.created", {"id": 7}, broadcast=True)
    transport = FakeTransport(200)
    report = deliver_due(transport=transport)
    assert report.succeeded == 1
    url, body, headers = transport.calls[0]
    assert url == hook.url
    assert json.loads(body)["data"] == {"id": 7}
    assert json.loads(body)["type"] == "order.created"
    verify_signature(hook.secret, headers, body)
    assert headers["webhook-id"] == f"msg_{event.pk}"
    delivery = WebhookDelivery.objects.get()
    assert (delivery.status, delivery.attempts, delivery.last_status_code) == ("succeeded", 1, 200)
    assert deliver_due(transport=transport).succeeded == 0  # nothing due anymore


def test_retries_with_backoff_then_gives_up():
    endpoint()
    publish("order.created", {}, broadcast=True)
    now = timezone.now()
    transport = FakeTransport(500, OSError("boom"), 503)
    schedule = (timedelta(seconds=5), timedelta(minutes=1))
    assert deliver_due(transport=transport, schedule=schedule, now=now).retrying == 1
    delivery = WebhookDelivery.objects.get()
    assert delivery.last_error == "HTTP 500"
    assert delivery.next_attempt_at == now + timedelta(seconds=5)
    assert deliver_due(transport=transport, schedule=schedule, now=now).retrying == 0  # not due
    later = now + timedelta(seconds=6)
    deliver_due(transport=transport, schedule=schedule, now=later)
    delivery.refresh_from_db()
    assert delivery.last_error.startswith("OSError")
    report = deliver_due(transport=transport, schedule=schedule, now=later + timedelta(minutes=2))
    delivery.refresh_from_db()
    assert (report.failed, delivery.status, delivery.attempts) == (1, "failed", 3)


def test_failing_endpoints_are_disabled():
    hook = endpoint()
    publish("order.created", {}, broadcast=True)
    publish("order.updated", {}, broadcast=True)
    now = timezone.now()
    hook.failing_since = now - timedelta(days=6)
    hook.save()
    report = deliver_due(transport=FakeTransport(500, 500), now=now)
    hook.refresh_from_db()
    assert report.disabled_endpoints == 1
    assert not hook.is_active
    assert set(WebhookDelivery.objects.values_list("status", flat=True)) == {"failed"}


def test_management_command(capsys):
    endpoint(url="http://127.0.0.1:9/unreachable")
    publish("order.created", {}, broadcast=True)
    call_command("devx_webhooks", "deliver", "--timeout", "0.5")
    assert "retrying=1" in capsys.readouterr().out


def test_endpoint_api():
    ada = User.objects.create(username="ada")
    bob = User.objects.create(username="bob")

    class Endpoints(WebhookEndpointController):
        available_events = ("order.created", "order.paid")

    client = TestClient(Endpoints.as_router())
    rejected = client.post("/", json={"url": "https://a.example/h", "events": ["user.*"]}, user=ada)
    assert rejected.status_code == 422
    created = client.post("/", json={"url": "https://a.example/h", "events": ["order.*"]}, user=ada)
    assert created.status_code == 201, created.json()
    body = created.json()
    assert body["secret"].startswith("whsec_")
    pk = body["id"]
    assert "secret" not in client.get(f"/{pk}", user=ada).json()
    assert client.get(f"/{pk}", user=bob).status_code in {403, 404}
    assert client.get("/", user=bob).json() == []

    rotated = client.post(f"/{pk}/rotate-secret", user=ada).json()["secret"]
    assert rotated != body["secret"]
    ping = client.post(f"/{pk}/ping", user=ada)
    assert ping.status_code == 202
    assert ping.json()["event_type"] == "webhook.ping"
    deliver_due(transport=FakeTransport(500))
    deliveries = client.get(f"/{pk}/deliveries", user=ada).json()
    assert deliveries[0]["last_error"] == "HTTP 500"
    retried = client.post(f"/{pk}/deliveries/{deliveries[0]['id']}/retry", user=ada)
    assert retried.json()["status"] == "pending"
    assert client.patch(f"/{pk}", json={"events": ["order.paid"]}, user=ada).json()["events"] == [
        "order.paid"
    ]


# --- SSRF protection ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("http://example.com/hook", "scheme"),
        ("ftp://example.com/hook", "scheme"),
        ("https://user:pw@example.com/hook", "credentials"),
        ("https://localhost/hook", "local machine"),
        ("https://api.localhost/hook", "local machine"),
        ("https://127.0.0.1/hook", "private"),
        ("https://10.1.2.3/hook", "private"),
        ("https://169.254.169.254/latest/meta-data", "private"),
        ("https://[::1]/hook", "private"),
        ("https://[::ffff:192.168.0.1]/hook", "private"),
        ("https://0.0.0.0/hook", "private"),
    ],
)
def test_unsafe_urls_are_rejected(url, message):
    with pytest.raises(UnsafeURL, match=message):
        check_url(url)


def test_policy_relaxations():
    check_url("https://hooks.example.com/x")
    check_url("https://93.184.216.34/x")
    check_url("http://example.com/x", URLPolicy(allow_http=True))
    check_url("http://127.0.0.1:8000/x", URLPolicy(allow_http=True, allow_private_networks=True))


@pytest.fixture
def local_server():
    import http.server
    import threading

    received = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1], received
    server.shutdown()
    server.server_close()


def test_names_resolving_to_private_addresses_are_refused(local_server, monkeypatch):
    port, received = local_server
    real = socket.getaddrinfo

    def rebinding(host, *args, **kwargs):  # a public-looking name pointing inside
        return real("127.0.0.1" if host == "hooks.attacker.test" else host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", rebinding)
    url = f"http://hooks.attacker.test:{port}/"
    blocked = SafeHTTPTransport(URLPolicy(allow_http=True))
    with pytest.raises(UnsafeURL, match="non-public"):
        blocked(url, b"{}", {}, 2)
    assert received == []

    allowed = SafeHTTPTransport(URLPolicy(allow_http=True, allow_private_networks=True))
    assert allowed(url, b"{}", {"content-type": "application/json"}, 2) == 204
    assert received == [b"{}"]


def test_worker_records_blocked_deliveries():
    endpoint(url="https://10.0.0.8/hook")
    publish("order.created", {}, broadcast=True)
    report = deliver_due()
    assert report.retrying == 1
    assert "private" in WebhookDelivery.objects.get().last_error


def test_api_rejects_unsafe_urls():
    ada = User.objects.create(username="ada")
    client = TestClient(WebhookEndpointController.as_router())
    response = client.post("/", json={"url": "https://169.254.169.254/", "events": ["*"]}, user=ada)
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "payload", "url"]
    ok = client.post("/", json={"url": "https://hooks.example.com/", "events": ["*"]}, user=ada)
    patched = client.patch(f"/{ok.json()['id']}", json={"url": "https://localhost/"}, user=ada)
    assert patched.status_code == 422


# --- Secret encryption --------------------------------------------------------------


def test_secrets_are_encrypted_at_rest_and_rotated(capsys):
    from cryptography.fernet import Fernet
    from django.test import override_settings

    from ninja_devx.contrib.webhooks.secrets import is_encrypted

    plain = endpoint()  # created before encryption was enabled
    raw_plain = plain.secret
    first, second = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    with override_settings(NINJA_DEVX={"WEBHOOK_SECRET_KEYS": [first]}):
        ada = User.objects.create(username="ada")
        client = TestClient(WebhookEndpointController.as_router())
        created = client.post(
            "/", json={"url": "https://h.example.com/", "events": ["*"]}, user=ada
        )
        raw = created.json()["secret"]
        stored = WebhookEndpoint.objects.get(pk=created.json()["id"])
        assert raw.startswith("whsec_")
        assert is_encrypted(stored.secret)
        assert raw not in stored.secret
        assert stored.signing_secret == raw

        call_command("devx_webhooks", "encrypt-secrets")
        plain.refresh_from_db()
        assert is_encrypted(plain.secret)
        assert plain.signing_secret == raw_plain

        publish("order.created", {}, broadcast=True)
        transport = FakeTransport(200, 200)
        deliver_due(transport=transport)
        for _, body, headers in transport.calls:
            verify_signature([raw, raw_plain], headers, body)

    with override_settings(NINJA_DEVX={"WEBHOOK_SECRET_KEYS": [second, first]}):
        before = WebhookEndpoint.objects.get(pk=stored.pk).secret
        call_command("devx_webhooks", "encrypt-secrets")
        after = WebhookEndpoint.objects.get(pk=stored.pk)
        assert after.secret != before
        assert after.signing_secret == raw
    with override_settings(NINJA_DEVX={"WEBHOOK_SECRET_KEYS": [second]}):  # old key retired
        assert WebhookEndpoint.objects.get(pk=stored.pk).signing_secret == raw

    call_command("devx_webhooks", "generate-key")
    assert len(capsys.readouterr().out.strip().splitlines()[-1]) == 44


# --- Task queues --------------------------------------------------------------------


def test_publish_enqueues_delivery_after_commit(django_capture_on_commit_callbacks, monkeypatch):
    pytest.importorskip("django.tasks")
    from ninja_devx.contrib.webhooks.network import SafeHTTPTransport
    from ninja_devx.layers.tasks import OnCommitTaskQueue

    sent = []
    monkeypatch.setattr(
        SafeHTTPTransport,
        "__call__",
        lambda self, url, body, headers, timeout: sent.append(url) or 200,
    )
    endpoint()
    with django_capture_on_commit_callbacks(execute=True):
        publish("order.created", {"id": 1}, queue=OnCommitTaskQueue(), broadcast=True)
        assert sent == []  # nothing before commit
    assert sent == ["https://example.com/hook"]
    assert WebhookDelivery.objects.get().status == "succeeded"


def test_publish_with_a_custom_task(django_capture_on_commit_callbacks):
    from ninja_devx.layers.tasks import RecordingTaskQueue

    class Task:
        def enqueue(self, event_id, using):
            raise AssertionError("recorded, not run")

    queue = RecordingTaskQueue()
    task = Task()
    with django_capture_on_commit_callbacks(execute=True):
        event = publish("order.created", {}, queue=queue, task=task, broadcast=True)
        assert queue.calls == []
    assert queue.calls == [(task, (str(event.pk), "default"), {})]


def test_deliver_event_only_sends_that_event():
    endpoint()
    first = publish("order.created", {"n": 1}, broadcast=True)
    publish("order.created", {"n": 2}, broadcast=True)
    transport = FakeTransport()
    from ninja_devx.contrib.webhooks import outbox

    report = outbox.deliver_due(transport=transport, event_id=str(first.pk))
    assert report.succeeded == 1
    assert json.loads(transport.calls[0][1])["data"] == {"n": 1}


def test_publish_requires_explicit_audience_and_isolates_owner_and_tenant():
    ada = User.objects.create(username="ada")
    bob = User.objects.create(username="bob")
    personal = endpoint(owner=ada)
    tenant_a = endpoint(owner=ada, tenant_key="a")
    tenant_b = endpoint(owner=ada, tenant_key="b")
    bob_a = endpoint(owner=bob, tenant_key="a")
    endpoint(owner=bob)
    with pytest.raises(ValueError, match="requires owner"):
        publish("order.created", {"private": True})
    assert not OutboxEvent.objects.exists()
    with pytest.raises(ValueError, match="cannot be combined"):
        publish("order.created", {}, owner=ada, broadcast=True)
    event = publish("order.created", {}, owner=ada)
    assert set(event.deliveries.values_list("endpoint_id", flat=True)) == {personal.pk}
    event = publish("order.created", {}, owner=ada, tenant_key="a")
    assert set(event.deliveries.values_list("endpoint_id", flat=True)) == {tenant_a.pk}
    event = publish("order.created", {}, tenant_key="a")
    assert set(event.deliveries.values_list("endpoint_id", flat=True)) == {tenant_a.pk, bob_a.pk}
    assert tenant_b.pk not in event.deliveries.values_list("endpoint_id", flat=True)


def test_tenant_endpoint_api_does_not_accept_client_scope_or_cross_tenant_access():
    ada = User.objects.create(username="ada")
    other = endpoint(owner=ada, tenant_key="b")

    class Endpoints(WebhookEndpointController):
        tenant_field = "tenant_key"

    client = TestClient(Endpoints.as_router())
    assert client.get("/", user=ada).status_code == 403
    created = client.post(
        "/",
        user=ada,
        tenant="a",
        json={
            "url": "https://a.example/h",
            "events": ["order.*"],
            "tenant_key": "b",
        },
    )
    assert created.status_code == 201
    assert WebhookEndpoint.objects.get(pk=created.json()["id"]).tenant_key == "a"
    assert client.get(f"/{other.pk}", user=ada, tenant="a").status_code == 404
    assert TestClient(WebhookEndpointController.as_router()).get("/", user=ada).json() == []


def test_delivery_rechecks_audience_after_endpoint_owner_change():
    ada = User.objects.create(username="ada")
    bob = User.objects.create(username="bob")
    hook = endpoint(owner=ada)
    publish("order.created", {"private": True}, owner=ada)
    WebhookEndpoint.objects.filter(pk=hook.pk).update(owner=bob)
    send = FakeTransport()
    deliver_due(transport=send)
    assert send.calls == []


def test_batch_claims_just_in_time_and_signs_each_attempt_at_current_time(monkeypatch):
    first = endpoint()
    second = endpoint(url="https://second.example/h")
    publish("order.created", {}, broadcast=True)
    start = timezone.now()
    current = [start]
    monkeypatch.setattr("ninja_devx.contrib.webhooks.outbox.timezone.now", lambda: current[0])
    calls = []

    def send(url, body, headers, timeout):
        calls.append((url, headers["webhook-timestamp"]))
        if url == first.url:
            # A queued row has no lease until a worker is actually ready to send it.
            assert WebhookDelivery.objects.get(endpoint=second).lease_token is None
            current[0] += timedelta(seconds=1)
            competing = deliver_due(transport=send)
            assert competing.succeeded == 1
        return 200

    report = deliver_due(transport=send)
    assert report.succeeded == 1
    assert calls == [
        (first.url, str(int(start.timestamp()))),
        (second.url, str(int((start + timedelta(seconds=1)).timestamp()))),
    ]
    assert list(WebhookDelivery.objects.values_list("attempts", flat=True)) == [1, 1]


def test_late_worker_cannot_overwrite_new_lease_or_reset_endpoint_failure():
    import uuid

    hook = endpoint(failing_since=timezone.now() - timedelta(days=1))
    publish("order.created", {}, broadcast=True)
    replacement = uuid.uuid4()

    def stale_send(url, body, headers, timeout):
        WebhookDelivery.objects.update(lease_token=replacement)
        return 200

    report = deliver_due(transport=stale_send)
    delivery = WebhookDelivery.objects.get()
    hook.refresh_from_db()
    assert report.lost_claims == 1
    assert report.succeeded == 0
    assert delivery.lease_token == replacement
    assert delivery.status == "pending"
    assert delivery.attempts == 0
    assert hook.failing_since is not None


def test_retry_api_cannot_steal_a_running_delivery():
    import uuid

    owner = User.objects.create(username="owner")
    hook = endpoint(owner=owner)
    publish("order.created", {}, owner=owner)
    delivery = WebhookDelivery.objects.get()
    WebhookDelivery.objects.filter(pk=delivery.pk).update(lease_token=uuid.uuid4())
    response = TestClient(WebhookEndpointController.as_router()).post(
        f"/{hook.pk}/deliveries/{delivery.pk}/retry",
        user=owner,
    )
    assert response.status_code == 409


def test_maintenance_preserves_pending_work_and_retries_only_unclaimed(capsys):
    import uuid

    from django.core.management.base import CommandError

    endpoint()
    completed = publish("order.created", {}, broadcast=True)
    pending = publish("order.created", {}, broadcast=True)
    deliver_due(transport=FakeTransport(), event_id=str(completed.pk))
    OutboxEvent.objects.update(created=timezone.now() - timedelta(days=40))
    call_command("devx_webhooks", "stats")
    stats = json.loads(capsys.readouterr().out)
    assert stats["counts"] == {"pending": 1, "succeeded": 1}
    assert stats["oldest_pending_seconds"] >= 40 * 86400
    call_command("devx_webhooks", "prune", "--days", "30")
    assert "removed_events=1" in capsys.readouterr().out
    assert list(OutboxEvent.objects.values_list("pk", flat=True)) == [pending.pk]
    delivery = WebhookDelivery.objects.get()
    WebhookDelivery.objects.update(lease_token=uuid.uuid4())
    with pytest.raises(CommandError, match="claimed"):
        call_command("devx_webhooks", "retry", "--delivery-id", str(delivery.pk))
    WebhookDelivery.objects.update(lease_token=None, status="failed", attempts=8)
    call_command("devx_webhooks", "retry", "--delivery-id", str(delivery.pk))
    delivery.refresh_from_db()
    assert delivery.status == "pending"
    assert delivery.attempts == 0
