"""Multi-tenancy: find the current tenant once per request and scope queries with it.

Model controllers with ``tenant_field`` filter every query by the tenant, set it on create,
reject input schemas that accept it, and 404 for other tenants' objects::

    class ProjectController(CRUDController[Project, ProjectOut, ProjectIn]):
        tenant_field = "organization"

The tenant comes from the first source that is configured:

1. ``tenant_resolver`` on the controller, then ``NINJA_DEVX["TENANT_RESOLVER"]``:
   a function of the request (sync or ``async def``) returning the tenant or ``None``;
2. ``tenant_context`` on the controller, then ``NINJA_DEVX["TENANT_CONTEXT"]``: a
   ``RequestContext[User, Tenant]`` key resolved from the controller's container;
3. ``request.tenant``, as set by tenant middleware (django-tenants and similar).

A request without a tenant gets 403. The tenant is cached on the request, so services can
read it with ``current_tenant(request)``.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Final, TypeAlias, cast

from asgiref.sync import sync_to_async
from django.http import HttpRequest
from django.utils.translation import gettext_noop

from ..layers.errors import PermissionDenied

if TYPE_CHECKING:
    from ..dependencies.instances import Invocation

__all__ = [
    "MissingTenant",
    "TenantResolver",
    "acurrent_tenant_for",
    "current_tenant",
    "current_tenant_for",
]

TenantResolver: TypeAlias = (
    Callable[[HttpRequest], object] | Callable[[HttpRequest], Awaitable[object]]
)
_TENANT_ATTR: Final = "_ninja_devx_tenant"
_UNSET: Final = object()


class MissingTenant(PermissionDenied):
    """No tenant is associated with this request."""

    default_message = gettext_noop("No tenant is associated with this request.")

    code = "tenant_required"


def current_tenant(request: HttpRequest) -> object | None:
    """The tenant already resolved for this request, or ``None``.

    :param request: The current request.
    """
    tenant: object = request.__dict__.get(_TENANT_ATTR, None)
    return tenant


def current_tenant_for(
    request: HttpRequest,
    *,
    resolver: TenantResolver | None,
    context: object | None,
    invocation: Invocation | None,
) -> object:
    """Resolve (once) and return the tenant; raises ``MissingTenant`` (403) without one."""
    cached: object = request.__dict__.get(_TENANT_ATTR, _UNSET)
    if cached is not _UNSET:
        return _required(cached)
    tenant: object = None
    if resolver is not None:
        result = resolver(request)
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise TypeError("an async tenant resolver needs an async operation")
        tenant = result
    elif context is not None and invocation is not None and invocation.resolver is not None:
        tenant = getattr(invocation.resolver.resolve(cast("type[object]", context)), "tenant", None)
    else:
        tenant = getattr(request, "tenant", None)
    request.__dict__[_TENANT_ATTR] = tenant
    return _required(tenant)


async def acurrent_tenant_for(
    request: HttpRequest,
    *,
    resolver: TenantResolver | None,
    context: object | None,
    invocation: Invocation | None,
) -> object:
    """``current_tenant_for`` for async operations: sync resolvers run in one thread hop."""
    cached: object = request.__dict__.get(_TENANT_ATTR, _UNSET)
    if cached is not _UNSET:
        return _required(cached)
    tenant: object = None
    if resolver is not None:
        if inspect.iscoroutinefunction(resolver):
            tenant = await resolver(request)
        else:
            tenant = await sync_to_async(resolver)(request)
    elif context is not None and invocation is not None:
        key = cast("type[object]", context)
        if invocation.async_resolver is not None:
            resolved: object = await invocation.async_resolver.aresolve(key)
        elif invocation.resolver is not None:
            resolved = invocation.resolver.resolve(key)
        else:
            resolved = None
        tenant = getattr(resolved, "tenant", None)
    else:
        tenant = getattr(request, "tenant", None)
    request.__dict__[_TENANT_ATTR] = tenant
    return _required(tenant)


def _required(tenant: object) -> object:
    if tenant is None:
        raise MissingTenant("No tenant is associated with this request")
    return tenant
