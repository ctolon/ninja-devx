from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path
from ninja import NinjaAPI
from ninja.security import django_auth
from ninja_devx import Container, mount

from orders.api import OrderController
from orders.payments import PaymentGateway, gateway

container = Container()
container.scoped(PaymentGateway, gateway)

api = NinjaAPI(title="Orders", version="1.0.0", auth=django_auth)
mount(api, {"/orders": OrderController}, container=container)

urlpatterns = [
    path("api/", api.urls),
    path("accounts/login/", LoginView.as_view(), name="login"),
    path("accounts/logout/", LogoutView.as_view(), name="logout"),
]
