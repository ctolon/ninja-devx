"""The composition root: Django implementations for the ports."""

from django.contrib.auth.models import User
from ninja_devx import Container, request_context
from ninja_devx.layers import ModelRepository, Repository, RequestContext

from shop.models import Order, Product


def build_container() -> Container:
    container = Container()
    container.scoped(Repository[Product], lambda: ModelRepository(Product))
    container.scoped(Repository[Order], lambda: ModelRepository(Order))
    container.scoped(RequestContext[User, None], request_context(User))
    return container
