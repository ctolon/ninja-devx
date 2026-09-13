# Webhooks

!!! tip "Reference"

    [configuration reference](../options/contrib.md#webhooks-ninja_devxcontribwebhooks)

Notify other systems when something happens, without losing events and without
notifying anyone about changes that were rolled back.

```python
INSTALLED_APPS += ["ninja_devx.contrib.webhooks"]  # then: manage.py migrate
```

## Publishing events

```python
from ninja_devx.contrib.webhooks.outbox import publish


class OrderService(ModelService[Order]):
    @dual
    def create(self, data: Mapping[str, object]) -> Order:
        with transaction.atomic():
            order = super().create(data)
            publish("order.created", {"id": order.pk, "total": order.total}, owner=order.owner)
        return order
```

`publish` is a **transactional outbox**. It writes the event, plus one pending delivery
per subscribed endpoint, in your transaction. Nothing is sent from the request, so a slow
or failing receiver is handled by the worker. Outbox persistence and queue dispatch can still fail; monitor both.


### Audience and tenant isolation

Every call must specify `owner=...`, a nonempty `tenant_key=...`, or explicit
`broadcast=True`. Omitting the audience raises before writing an event. Owner-only
publication reaches personal endpoints (`tenant_key=""`). Combining owner and tenant
requires both to match. Tenant-only publication reaches subscribers within that tenant.
`broadcast=True` deliberately reaches all subscribers and cannot be combined with a target.
Use it only for public events. Event patterns never grant access to another audience.

For tenant-managed endpoints, use `tenant_field = "tenant_key"` and a server-side tenant
resolver returning the same stable string passed to `publish(tenant_key=...)`. The generic
tenant/owner filters then protect create, list, update, history and retry. The input schema
does not accept the tenant key. The default personal controller only sees empty-tenant
endpoints. Endpoint audience membership is checked again before delivery.

`using=` must identify the same database as the business transaction. Queue callbacks run
only after that database commits, even with an immediate queue. Custom task wrappers now
accept `(event_id, database_alias)`. Existing events with no audience are not sent until an
operator explicitly assigns their intended audience; do not mark them all as broadcasts.

## Delivering

Run a worker next to your web processes (a systemd service, a container, or cron without
`--loop`):

```bash
manage.py devx_webhooks deliver --loop --limit 100 --timeout 10
```

or call `deliver_due()` from your task queue. Each attempt sends:

```http
POST /hooks/orders HTTP/1.1
content-type: application/json
webhook-id: msg_7c0e7a55-3c9a-4bd4-9a51-3f0a3e4c2c11
webhook-timestamp: 1767225600
webhook-signature: v1,K5oZfzN95Z9UVu1EsfQmfVNQhnkZ2pj9o9NDN/H/pI4=

{"type":"order.created","timestamp":"2026-01-01T00:00:00+00:00","data":{"id":42,"total":"99.90"}}
```

- Signatures follow the [Standard Webhooks](https://www.standardwebhooks.com) format
  (HMAC-SHA256 of `id.timestamp.body`), so receivers can use any Standard Webhooks library.
- A 2xx response is success. Other statuses, timeouts and connection errors are retried
  after 5 s, 5 min, 30 min, 2 h, 5 h, 10 h and 10 h (`schedule=`); after that the delivery
  is `failed`.
- An endpoint failing continuously for 5 days is disabled (`disable_after=`), and its
  pending deliveries are marked failed.
- Several workers can run at once. Each delivery is claimed with a conditional update
  immediately before sending. A unique token prevents a late worker from overwriting a
  newer attempt. Requests run outside transactions, with a fresh signing timestamp per
  attempt. A dead worker's delivery becomes due after `timeout + 60` seconds. Delivery is
  **at least once**: a receiver must deduplicate by `webhook-id`, because process pauses
  or a response lost after receipt can still cause a retry. Custom transports must enforce
  their timeout. Manual retry refuses rows held by a worker.
- Redirects are not followed, and proxies from the environment are ignored. Pass
  `transport=` to use another HTTP client.

## Delivering right after commit

A worker adds up to `--interval` seconds of delay. To send as soon as the transaction
commits, enqueue the delivery as well. The worker still handles retries:

```python
from ninja_devx.layers.tasks import OnCommitTaskQueue

publish("order.created", {"id": order.pk}, owner=order.owner, queue=OnCommitTaskQueue())
```

- On Django 6.0+, the default task is `ninja_devx.contrib.webhooks.tasks.deliver_event_task`
  (`django.tasks`), which runs on whatever `TASKS` backend you configure.
- With Celery, RQ or another queue, pass your own task. It receives the event id and database alias and calls
  `deliver_event(event_id)`:

```python
from ninja_devx.contrib.webhooks.outbox import deliver_event


@shared_task
def deliver_webhook(event_id: str, using: str) -> None:
    deliver_event(event_id, using=using)


class CeleryDelivery:  # anything with enqueue(event_id)
    def enqueue(self, event_id: str, using: str) -> None:
        deliver_webhook.delay(event_id, using)


publish("order.created", payload, owner=order.owner, queue=OnCommitTaskQueue(), task=CeleryDelivery())
```

## Network safety (SSRF)

Endpoint URLs come from API clients, so a careless worker could be told to call
`http://169.254.169.254/` (cloud metadata), `http://localhost:8000/admin` or an internal
service. Webhooks refuse that by default:

- When an endpoint is saved (API or admin), `check_url` requires `https://`, rejects
  credentials in the URL, `localhost` names, and literal loopback, private, link-local and
  reserved addresses. The API answers 422.
- When sending, `SafeHTTPTransport` checks the address it actually connected to, after DNS
  resolution. A public-looking name that resolves to a private address (DNS rebinding) is
  refused, and the delivery is recorded as failed.

For development against local receivers, relax the policy explicitly, on both sides:

```python
class Endpoints(WebhookEndpointController):
    url_policy = URLPolicy(allow_http=True, allow_private_networks=True)
```

```bash
manage.py devx_webhooks deliver --loop --allow-http --allow-private-networks
```

## Encrypting secrets at rest

Signing needs the raw secret, so it cannot be hashed like an API key. Encrypt it with
[Fernet](https://cryptography.io/en/latest/fernet/) keys that live outside the database:

```bash
pip install "ninja-devx[crypto]"
manage.py devx_webhooks generate-key
```

```python
NINJA_DEVX = {"WEBHOOK_SECRET_KEYS": [env("WEBHOOK_KEY")]}
```

```bash
manage.py devx_webhooks encrypt-secrets  # encrypts existing secrets
```

- New and rotated secrets are stored as `fernet:<token>`. `endpoint.signing_secret` returns
  the raw value, and `endpoint.set_secret(raw)` stores one.
- To rotate the key, put the new key first, keep the old ones after it, run
  `encrypt-secrets`, then remove the old keys.
- Without keys, secrets are stored as they are (as before), so restrict database access.

## Endpoints API

`WebhookEndpointController` lets users manage their own endpoints:

```python
class Endpoints(WebhookEndpointController):
    available_events = ("order.created", "order.paid", "invoice.sent")


mount(api, {"/webhooks": Endpoints})
```

| Route | Description |
|---|---|
| `GET /`, `GET /{pk}` | the user's endpoints |
| `POST /` | `{"url", "events": ["order.*"], "description"}` → includes `secret` once (201) |
| `PATCH /{pk}`, `PUT /{pk}`, `DELETE /{pk}` | change or remove; `is_active: true` re-enables a disabled endpoint |
| `POST /{pk}/rotate-secret` | a new secret, shown once |
| `GET /{pk}/deliveries` | the last 100 deliveries with status, attempts and last error |
| `POST /{pk}/deliveries/{id}/retry` | queue a delivery again |
| `POST /{pk}/ping` | queue a `webhook.ping` event (202) |

Subscriptions are event types or patterns: `"order.created"`, `"order.*"`, `"*"`. With
`available_events` set, patterns that match none of them are rejected with 422.

## Receiving (verifying signatures)

```python
from ninja_devx.contrib.webhooks.signing import InvalidSignature, verify_signature


@csrf_exempt
def orders_hook(request: HttpRequest) -> HttpResponse:
    try:
        verify_signature(settings.ORDERS_WEBHOOK_SECRET, request.headers, request.body)
    except InvalidSignature:
        return HttpResponse(status=401)
    event = json.loads(request.body)
    ...
    return HttpResponse(status=204)
```

- Verify the raw body (`request.body`), not re-serialized JSON.
- Pass a list of secrets during a rotation: `verify_signature([new, old], ...)`.
- Requests older than `tolerance` seconds (default 300) are rejected to limit replays.
  Deduplicate on `webhook-id`, because retries reuse it.

## Admin

With `django.contrib.admin` installed, endpoints, deliveries and events appear in the admin:

- Creating an endpoint shows its secret once in a message. The same happens for the
  "Rotate the signing secret" action. The secret is never displayed otherwise.
- URLs are checked with the same policy as the API.
- The "Send again" action requeues deliveries, and "Enable and clear failures" reactivates
  disabled endpoints.

## Housekeeping

```bash
manage.py devx_webhooks stats --database default
manage.py devx_webhooks retry --delivery-id 42
manage.py devx_webhooks prune --days 30 --limit 1000
```

`stats` emits JSON counts and the oldest pending event's age. Track these together with
worker `retrying`, `failed`, `disabled_endpoints` and `lost_claims` counters. `retry` resets
one unclaimed delivery's retry budget; the receiver may already have processed it, so
keep receiver deduplication enabled. `prune` removes a bounded batch of old terminal events
and their deliveries. Pending work is retained, including expired worker claims; concurrent
retry is protected by row locks. Repeat pruning periodically. SQLite does not provide the
same row-lock guarantees as PostgreSQL for maintenance concurrent with retry.
