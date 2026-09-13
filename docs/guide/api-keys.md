# API keys

!!! tip "Reference"

    [configuration reference](../options/contrib.md#api-keys-ninja_devxcontribapikeys)

Machine clients such as CI jobs, integrations and scripts need credentials that can be
limited, rotated and revoked without touching a user's password. `ninja_devx.contrib.apikeys`
provides scoped keys through Ninja's own authentication classes.

```python
INSTALLED_APPS += ["ninja_devx.contrib.apikeys"]  # then: manage.py migrate
```

```python
from ninja.security import django_auth
from ninja_devx.contrib.apikeys.api import APIKeyController
from ninja_devx.contrib.apikeys.auth import APIKeyAuth, HasScopes, RequiresScope

api = NinjaAPI(auth=[APIKeyAuth(), django_auth])  # keys or sessions


class OrderController(Controller):
    options = ControllerOptions(permissions=[HasScopes(allow_unscoped=True)])

    @get("/", meta=(RequiresScope("orders:read"),))
    def list_orders(self, request: HttpRequest) -> list[Order]: ...

    @post("/", meta=(RequiresScope("orders:write"),))
    def create(self, request: HttpRequest, payload: OrderIn) -> Order: ...


mount(api, {"/orders": OrderController, "/me/api-keys": APIKeyController})
```

```bash
curl -H "X-API-Key: ndx_3f9a1c2b7d4e_..." https://api.example.com/orders/
```

## How keys work

- A key looks like `ndx_<prefix>_<secret>`. The prefix finds the row. The secret is stored
  only as a SHA-256 digest and compared in constant time, so a database leak does not
  reveal usable keys.
- The raw key is returned **once**, when the key is created.
- `request.auth` is the key's user, so owner fields, tenancy and permissions work
  unchanged. `current_api_key(request)` returns the key itself.
- Keys stop working when revoked, expired, or when their user is deactivated.
- `last_used_at` is updated at most once a minute per key.
- `APIKeyAuth` reads `X-API-Key`; `APIKeyBearer` reads `Authorization: Bearer ndx_...`.
- In async operations, authentication costs one thread hop.

## Scopes

`RequiresScope("orders:write")` is operation metadata; `HasScopes()` checks it against the
key's scopes:

| Key scopes | `orders:read` | `orders:write` | `invoices:read` |
|---|---|---|---|
| `["orders:read"]` | ✅ | ❌ 403 | ❌ 403 |
| `["orders:*"]` | ✅ | ✅ | ❌ 403 |
| `["*"]` | ✅ | ✅ | ✅ |

Operations without `RequiresScope` accept any key. Requests authenticated another way
(sessions, JWT) are refused on scoped operations unless `allow_unscoped=True`, because
they don't carry scopes.

## Rate limits per key

```python
from ninja_devx.contrib.apikeys.auth import APIKeyRateThrottle


class OrderController(Controller):
    options = ControllerOptions(throttle=[APIKeyRateThrottle("1000/hour")])
```

Each key has its own budget. A key's `rate_limit` field (`"10000/hour"` for a partner, set
in the admin, `create_api_key(rate_limit=...)` or `devx_apikey create --rate`) overrides the
throttle's default. Requests without an API key pass this throttle, so combine it with
`UserRateThrottle` or `AnonRateThrottle`. Responses carry `RateLimit-*` headers.

## Managing keys

`APIKeyController` lets users manage their own keys through session or other user
authentication. Requests authenticated by `APIKeyAuth` or `APIKeyBearer` receive 403
on all key-management operations, including tokens with `*`. Tokens cannot delegate
credentials or change their own authorization limits:

| Route | Description |
|---|---|
| `GET /` | `items` and `count` for the user's keys (never the secret) |
| `POST /` | `{"name", "scopes", "expires_at"}` → the key, with `key` shown once (201) |
| `DELETE /{key_id}` | revoke (204) |

Set `grantable_scopes = ("orders:read", "orders:write")` on a subclass to restrict what
users may request (422 otherwise).

From code or the command line:

```python
key, raw = create_api_key(
    user, "CI deploys", scopes=["orders:*"], expires_at=now() + timedelta(days=90)
)
revoke_api_key(key)
```

```bash
manage.py devx_apikey create --user ada --name "CI deploys" --scope orders:read --scope orders:write --days 90
manage.py devx_apikey revoke --prefix 3f9a1c2b7d4e
```

In the admin, keys can be searched, edited (name, scopes, rate limit, expiry) and revoked
with the "Revoke selected API keys" action. They cannot be created there, because the raw
key has to be shown once.

## Testing

```python
def test_scopes(ninja_client, django_user_model):
    user = django_user_model.objects.create(username="ci")
    _, raw = create_api_key(user, "test", scopes=["orders:read"])
    client = ninja_client(OrderController)
    assert client.get("/", headers={"X-API-Key": raw}).status_code == 200
    assert client.post("/", json={...}, headers={"X-API-Key": raw}).status_code == 403
```

Key listing accepts `page` and `page_size`, defaults to 50 items, and caps each page at 100.
