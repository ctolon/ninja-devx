# Browser and mobile clients

Which credential a client uses decides most of the setup:

| Client | Credential | Ninja `auth=` |
|---|---|---|
| Single-page app on the same site as the API (`app.example.com` → `api.example.com`) | Django session cookie + CSRF token | `django_auth` |
| Mobile or desktop app | Token in a header, issued by django-allauth's headless API | `x_session_token_auth` or `jwt_token_auth` |
| Scripts, CI jobs, other servers | Scoped API key | [`APIKeyAuth()`](api-keys.md) |

Several can be accepted at once: `NinjaAPI(auth=[django_auth, APIKeyAuth()])`.

## Single-page apps: sessions and CSRF

Sessions keep the credential in an `HttpOnly` cookie that scripts cannot read, and Django's
login, logout, password validation and session expiry apply unchanged. Ninja checks the
CSRF token for every cookie-authenticated request whose method is not `GET`, `HEAD`,
`OPTIONS` or `TRACE`.

```python
from django.contrib.auth import authenticate, login, logout
from django.middleware.csrf import get_token
from django.views.decorators.csrf import csrf_protect
from ninja import Schema, Status
from ninja.decorators import decorate_view
from ninja.security import django_auth

from ninja_devx import Controller, get, post


class Credentials(Schema):
    username: str
    password: str


class SessionController(Controller):
    @get("/csrf", auth=None)
    def csrf(self, request) -> dict[str, str]:
        return {"csrf_token": get_token(request)}

    @post("/login", auth=None, response={204: None, 401: dict})
    @decorate_view(csrf_protect)  # no auth means no automatic CSRF check
    def login(self, request, credentials: Credentials):
        user = authenticate(request, **credentials.model_dump())
        if user is None:
            return Status(401, {"detail": "Invalid credentials."})
        login(request, user)
        return Status(204, None)

    @post("/logout", auth=django_auth, response={204: None})
    def logout(self, request):
        logout(request)
        return Status(204, None)
```

The client fetches the token once, then sends it back as `X-CSRFToken` with every unsafe
request, and with cookies included:

```ts
const { csrf_token } = await (await fetch(`${API}/session/csrf`, { credentials: "include" })).json();

await fetch(`${API}/session/login`, {
  method: "POST",
  credentials: "include",
  headers: { "Content-Type": "application/json", "X-CSRFToken": csrf_token },
  body: JSON.stringify({ username, password }),
});
```

The token comes back in the response body instead of being read from the `csrftoken`
cookie because a page on `app.example.com` cannot read a cookie set by `api.example.com`.
Fetch it again after login: Django rotates the token when the user changes.

When the app and the API are on different origins of the same site, allow the app's
origin explicitly ([django-cors-headers](https://github.com/adamchainz/django-cors-headers)
for the CORS part):

```python
CSRF_TRUSTED_ORIGINS = ["https://app.example.com"]
CORS_ALLOWED_ORIGINS = ["https://app.example.com"]
CORS_ALLOW_CREDENTIALS = True
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = True
# SameSite=Lax (the default) is enough within one site. An app on another site entirely
# would need SameSite=None, which browsers increasingly block: use tokens there instead.
```

`QUERY` operations are safe by the HTTP specification, but Django's CSRF middleware does not
know the method yet and treats it as unsafe, so send `X-CSRFToken` with them too.

## Mobile apps: django-allauth headless tokens

Native apps do not share a cookie jar with a browser and have no CSRF exposure, so they
send a token header. [django-allauth](https://docs.allauth.org/)'s headless API provides
the login, signup, email verification, MFA and social login endpoints, and security
classes for Ninja:

```python
from allauth.headless.contrib.ninja.security import jwt_token_auth, x_session_token_auth

api = NinjaAPI(auth=[x_session_token_auth, django_auth])  # app tokens or browser sessions
```

`x_session_token_auth` reads the `X-Session-Token` header that allauth's `app` client
receives after login. `jwt_token_auth` accepts access tokens when allauth is configured
with its JWT token strategy. ninja-devx permissions (`IsAuthenticated`, `IsOwner`, object
permissions) take the user from `request.auth` when an auth class returns a user, else from
`request.user`, so they apply the same way as with sessions.

Prefer these over writing a token system: issuing, refreshing, revoking and rate limiting
tokens is where hand-written authentication usually goes wrong.

## Machine clients

Integrations that act on their own behalf rather than for a signed-in user use
[API keys](api-keys.md): scoped, hashed at rest, rotated and revoked without touching a
password.
