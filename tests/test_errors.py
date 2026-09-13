import pytest
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import override_settings
from ninja import NinjaAPI
from ninja.testing import TestClient

from ninja_devx import Controller, ControllerConfigError, ControllerOptions, get, mount
from ninja_devx.http.errors import ErrorMap
from ninja_devx.layers import Conflict, NotFound, ValidationFailed


class OutOfStock(Exception):
    def __init__(self, sku: str) -> None:
        super().__init__(f"{sku} is out of stock")
        self.sku = sku


class PaymentDeclined(Exception):
    """Mappable without importing anything: two class attributes."""

    http_status = 402
    code = "payment_declined"

    def error_body(self) -> dict[str, object]:
        return {"detail": "Card declined", "retry": False}


class SubOutOfStock(OutOfStock):
    pass


class ShopController(Controller):
    options = ControllerOptions(errors=ErrorMap().map(OutOfStock, 409, code="out_of_stock"))

    @get("/stock/{sku}", raises=(OutOfStock,))
    def stock(self, request, sku: str):
        raise SubOutOfStock(sku)

    @get("/custom-body", errors=ErrorMap().map(OutOfStock, 410, body=lambda exc: {"sku": exc.sku}))
    def custom_body(self, request):
        raise OutOfStock("B1")

    @get("/domain")
    def domain(self, request):
        raise Conflict("Already taken", field="slug")

    @get("/not-found")
    def not_found(self, request):
        raise NotFound()

    @get("/validation")
    def validation(self, request):
        raise ValidationFailed(errors={"slug": ["taken"]})

    @get("/django-validation")
    def django_validation(self, request):
        raise DjangoValidationError({"email": ["invalid"]})

    @get("/django-permission")
    def django_permission(self, request):
        raise DjangoPermissionDenied()

    @get("/duck")
    async def duck(self, request):
        raise PaymentDeclined()

    @get("/unmapped")
    def unmapped(self, request):
        raise RuntimeError("bug")


@pytest.fixture
def client():
    return TestClient(ShopController.as_router())


def test_controller_rules_follow_the_exception_mro(client):
    response = client.get("/stock/A1")
    assert (response.status_code, response.json()) == (
        409,
        {"detail": "A1 is out of stock", "code": "out_of_stock"},
    )


def test_operation_rules_win_and_can_shape_the_body(client):
    response = client.get("/custom-body")
    assert (response.status_code, response.json()) == (410, {"sku": "B1"})


def test_domain_errors_map_themselves(client):
    assert client.get("/domain").json() == {
        "detail": "Already taken",
        "code": "conflict",
        "field": "slug",
    }
    assert client.get("/domain").status_code == 409
    assert client.get("/not-found").status_code == 404
    validation = client.get("/validation")
    assert validation.status_code == 422
    assert validation.json()["detail"] == [
        {"type": "validation_failed", "loc": ["body", "slug"], "msg": "taken"}
    ]


def test_django_exceptions_have_defaults(client):
    assert client.get("/django-validation").status_code == 422
    assert client.get("/django-permission").status_code == 403


async def test_duck_typed_errors_in_async_operations():
    from ninja.testing import TestAsyncClient

    response = await TestAsyncClient(ShopController.as_router()).get("/duck")
    assert (response.status_code, response.json()) == (
        402,
        {"detail": "Card declined", "retry": False, "code": "payment_declined"},
    )


def test_unmapped_exceptions_propagate(client):
    with pytest.raises(RuntimeError, match="bug"):
        client.get("/unmapped")


def test_problem_json_format(client):
    with override_settings(NINJA_DEVX={"ERROR_FORMAT": "problem+json"}):
        response = client.get("/stock/A1")
    assert response["Content-Type"] == "application/problem+json"
    assert response.json() == {
        "type": "about:blank",
        "title": "Conflict",
        "status": 409,
        "code": "out_of_stock",
        "detail": "A1 is out of stock",
    }


def test_project_rules_from_settings():
    class Plain(Controller):
        @get("/")
        def index(self, request):
            raise OutOfStock("C")

    rules = ErrorMap().map(OutOfStock, 409)
    with override_settings(NINJA_DEVX={"ERRORS": rules}):
        response = TestClient(Plain.as_router()).get("/")
    assert (response.status_code, response.json()["code"]) == (409, "out_of_stock")


def test_raises_are_documented_and_checked():
    api = NinjaAPI()
    api.add_router("", ShopController.as_router())
    responses = api.get_openapi_schema(path_prefix="")["paths"]["/stock/{sku}"]["get"]["responses"]
    assert 409 in responses

    class Undeclared(Controller):
        @get("/", raises=(KeyError,))
        def index(self, request): ...

    with pytest.raises(ControllerConfigError, match="no error rule maps it"):
        Undeclared.as_router()


def test_install_uses_ninja_exception_handlers():
    def plain_view(request):
        raise OutOfStock("D")

    api = NinjaAPI(urls_namespace="errors-install")
    api.get("/plain")(plain_view)
    ErrorMap().map(OutOfStock, 409).install(api)
    response = TestClient(api).get("/plain")
    assert response.status_code == 409


def test_mount_installs_errors_on_the_api():
    class Other(Controller):
        @get("/")
        def index(self, request):
            raise OutOfStock("E")

    def plain_view(request):
        raise OutOfStock("F")

    api = NinjaAPI(urls_namespace="errors-mount")
    api.get("/plain")(plain_view)
    mount(api, {"/other": Other}, errors=ErrorMap().map(OutOfStock, 409))
    client = TestClient(api)
    assert client.get("/other/").status_code == 409
    assert client.get("/plain").status_code == 409  # installed with api.add_exception_handler
