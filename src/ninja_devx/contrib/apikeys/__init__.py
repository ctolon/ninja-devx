"""Scoped API keys: hashed at rest, shown once, checked by Ninja authentication.

``INSTALLED_APPS += ["ninja_devx.contrib.apikeys"]``, then::

    api = NinjaAPI(auth=[APIKeyAuth(), django_auth])

    class OrderController(Controller):
        options = ControllerOptions(permissions=[HasScopes()])

        @post("/", meta=(RequiresScope("orders:write"),))
        def create(self, request: HttpRequest, payload: OrderIn) -> Order: ...

Keys look like ``ndx_<prefix>_<secret>``: the prefix finds the row, the secret is stored
as a SHA-256 digest and compared in constant time. Create them with
``manage.py devx_apikey create`` or ``APIKeyController``.
"""
