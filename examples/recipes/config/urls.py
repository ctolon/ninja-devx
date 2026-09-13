from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path
from ninja import NinjaAPI
from ninja.security import django_auth
from ninja_devx import mount

from cosmic.api import OrderController as CosmicOrders
from cosmic.wiring import build_container
from hacksoft.api import OrderController as HackSoftOrders
from interactors.api import OrderController as DishkaOrders
from interactors.wiring import build_resolver

api = NinjaAPI(title="Order recipes", version="1.0.0", auth=django_auth)
mount(api, {"/orders": HackSoftOrders}, prefix="/hacksoft")
mount(api, {"/orders": CosmicOrders}, prefix="/cosmic", container=build_container())
mount(api, {"/orders": DishkaOrders}, prefix="/dishka", container=build_resolver())

urlpatterns = [
    path("api/", api.urls),
    path("accounts/login/", LoginView.as_view(), name="login"),
    path("accounts/logout/", LogoutView.as_view(), name="logout"),
]
