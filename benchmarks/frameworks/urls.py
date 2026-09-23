"""URLs for the workload: one ``GET /items`` endpoint per available framework.

Optional frameworks (DRF, Ninja Extra, ninja-crud) are added only when importable, so the
workload runs with just Django Ninja and ninja-devx. ``ENDPOINTS`` maps a label to the URL
path used by ``overhead.py``.
"""

from __future__ import annotations

from typing import ClassVar

from django.urls import path
from ninja import NinjaAPI

from ninja_devx import Controller, get, mount

from .workload import ITEMS, ItemOut

ENDPOINTS: dict[str, str] = {}
urlpatterns: list[object] = []

# Plain Django Ninja function view.
plain = NinjaAPI(urls_namespace="plain")


@plain.get("/items", response=list[ItemOut])
def plain_items(request: object) -> list[dict[str, object]]:
    return ITEMS


urlpatterns.append(path("plain/", plain.urls))
ENDPOINTS["ninja"] = "/plain/items"

# ninja-devx controller (same schema, class-based).
devx = NinjaAPI(urls_namespace="devx")


class ItemsController(Controller):
    @get("/items", response=list[ItemOut])
    def list_items(self, request: object) -> list[dict[str, object]]:
        return ITEMS


mount(devx, {"/": ItemsController})
urlpatterns.append(path("devx/", devx.urls))
ENDPOINTS["ninja-devx"] = "/devx/items"

# Optional: Django REST framework APIView.
try:
    from rest_framework.response import Response
    from rest_framework.views import APIView
except ImportError:  # pragma: no cover - optional dependency
    pass
else:

    class DrfItems(APIView):
        authentication_classes: ClassVar[list[object]] = []
        permission_classes: ClassVar[list[object]] = []

        def get(self, request: object) -> Response:
            return Response(ITEMS)

    urlpatterns.append(path("drf/items", DrfItems.as_view()))
    ENDPOINTS["drf"] = "/drf/items"

# Optional: django-ninja-extra controller.
try:
    from ninja_extra import NinjaExtraAPI, api_controller, http_get
except ImportError:  # pragma: no cover - optional dependency
    pass
else:  # pragma: no cover - optional dependency
    try:
        extra = NinjaExtraAPI(urls_namespace="extra")

        @api_controller("/items", auth=None)
        class ExtraItems:
            @http_get("", response=list[ItemOut])
            def list_items(self) -> list[dict[str, object]]:
                return ITEMS

        extra.register_controllers(ExtraItems)
    except Exception:  # pragma: no cover - version differences; skip the adapter
        pass
    else:  # pragma: no cover - optional dependency
        urlpatterns.append(path("extra/", extra.urls))
        ENDPOINTS["ninja-extra"] = "/extra/items"
