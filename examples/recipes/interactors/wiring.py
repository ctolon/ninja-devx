from dishka import Provider, Scope, make_container
from django.contrib.auth.models import User
from django.http import HttpRequest
from ninja_devx import current_user
from ninja_devx.contrib.dishka import DishkaResolver, provide_controllers

from .api import OrderController
from .gateways import DjangoOrderGateway, OrderGateway
from .interactors import CancelOrderInteractor, ListOrdersInteractor, PlaceOrderInteractor


def acting_user(request: HttpRequest) -> User:
    return current_user(request, User)


def build_resolver() -> DishkaResolver:
    provider = Provider(scope=Scope.REQUEST)
    provider.from_context(provides=HttpRequest, scope=Scope.REQUEST)
    provider.provide(acting_user)
    provider.provide(DjangoOrderGateway, provides=OrderGateway, scope=Scope.APP)
    provider.provide_all(PlaceOrderInteractor, CancelOrderInteractor, ListOrdersInteractor)
    provide_controllers(provider, [OrderController])
    return DishkaResolver(make_container(provider))
