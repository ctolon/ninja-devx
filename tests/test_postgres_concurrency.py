"""Real row-lock and ownership contracts; run with TEST_DATABASE_URL set."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic

import pytest
from django.contrib.auth.models import User
from django.db import connection, connections
from ninja.testing import TestClient

from ninja_devx import Controller, idempotent, post
from ninja_devx.contrib.webhooks.models import WebhookDelivery
from ninja_devx.contrib.webhooks.outbox import deliver_due, publish
from tests.test_conditional import Notes
from tests.test_webhooks import endpoint
from tests.testapp.models import Note

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="requires real PostgreSQL"),
]


def worker(call, application_name):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('application_name', %s, false)", [application_name])
            cursor.execute("SET statement_timeout = '5s'")
        return call()
    finally:
        connections.close_all()


def test_two_conditional_writers_serialize_and_reject_stale_etag():
    held, release = Event(), Event()

    class LockedNotes(Notes):
        def perform_update(self, request, instance, data):
            if data.get("text") == "first":
                held.set()
                assert release.wait(4), "coordinator did not release the writer"
            return super().perform_update(request, instance, data)

    owner = User.objects.create(username="ada")
    note = Note.objects.create(owner=owner, text="original")
    first, second = [TestClient(LockedNotes.as_router()) for _ in range(2)]
    tag = first.get(f"/{note.pk}")["ETag"]
    second.get(f"/{note.pk}")  # initialize each client before worker threads start
    headers = {"If-Match": tag}
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(
            worker,
            lambda: first.patch(f"/{note.pk}", json={"text": "first"}, headers=headers),
            "cbv-etag-first",
        )
        try:
            assert held.wait(3)
            two = pool.submit(
                worker,
                lambda: second.patch(f"/{note.pk}", json={"text": "second"}, headers=headers),
                "cbv-etag-second",
            )
            deadline = monotonic() + 2
            blocked = False
            while monotonic() < deadline and not two.done():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT wait_event_type FROM pg_stat_activity WHERE application_name = %s",
                        ["cbv-etag-second"],
                    )
                    blocked = any(row[0] == "Lock" for row in cursor.fetchall())
                if blocked:
                    break
                Event().wait(0.01)
            assert blocked, "second writer did not wait on the actual database lock"
        finally:
            release.set()
        assert one.result(timeout=5).status_code == 200
        assert two.result(timeout=5).status_code == 412
    note.refresh_from_db()
    assert note.text == "first"


def test_idempotency_ownership_is_visible_to_another_database_connection():
    entered, release = Event(), Event()
    owner = User.objects.create(username="owner")

    class Payments(Controller):
        @post("/", decorators=[idempotent()])
        def pay(self, request):
            entered.set()
            assert release.wait(4)
            Note.objects.create(owner=owner, text="one payment")
            return {"ok": True}

    first, second = [TestClient(Payments.as_router()) for _ in range(2)]
    headers = {"Idempotency-Key": "one-payment"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(worker, lambda: first.post("/", headers=headers), "cbv-payment-first")
        try:
            assert entered.wait(3)
            two = pool.submit(
                worker, lambda: second.post("/", headers=headers), "cbv-payment-second"
            )
            assert two.result(timeout=3).status_code == 409
        finally:
            release.set()
        assert one.result(timeout=5).status_code == 200
    assert second.post("/", headers=headers)["Idempotent-Replayed"] == "true"
    assert Note.objects.count() == 1


def test_concurrent_webhook_workers_do_not_send_an_unexpired_claim_twice():
    entered, release = Event(), Event()
    endpoint()
    publish("order.created", {}, broadcast=True)
    calls = []

    def send(url, body, headers, timeout):
        calls.append(headers["webhook-id"])
        entered.set()
        assert release.wait(4)
        return 200

    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(worker, lambda: deliver_due(transport=send), "cbv-delivery-first")
        try:
            assert entered.wait(3)
            two = pool.submit(worker, lambda: deliver_due(transport=send), "cbv-delivery-second")
            assert two.result(timeout=3).succeeded == 0
        finally:
            release.set()
        assert one.result(timeout=5).succeeded == 1
    assert len(calls) == 1
    assert WebhookDelivery.objects.get().attempts == 1
