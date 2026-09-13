"""Typed access to the authenticated user, and building a ``RequestContext``."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar, overload

from asgiref.sync import sync_to_async
from django.http import HttpRequest
from django.utils.functional import empty
from ninja.errors import AuthenticationError

from ..layers.context import RequestContext

__all__ = [
    "AuthedRequest",
    "aauthenticated_user",
    "acurrent_user",
    "arequest_context",
    "arequest_user",
    "authenticated_user",
    "current_user",
    "request_context",
    "request_user",
]

UserT = TypeVar("UserT")
_USER_ATTR = "_ninja_devx_user"
TenantT = TypeVar("TenantT")


class AuthedRequest(HttpRequest, Generic[UserT]):
    """An ``HttpRequest`` whose ``auth`` (set by Ninja authentication) is ``UserT``.

    Use it to annotate ``request`` on operations that require authentication::

        @get("/me")
        def me(self, request: AuthedRequest[User]) -> User:
            return request.auth
    """

    auth: UserT


def request_user(request: HttpRequest) -> object | None:
    """The authenticated user: ``request.auth`` when it is a user, else ``request.user``.

    An unevaluated lazy ``request.user`` is not loaded inside an event loop (that
    would query the session synchronously); use ``arequest_user`` there.
    """
    auth: object = getattr(request, "auth", None)
    if getattr(auth, "is_authenticated", False):
        return auth
    loaded: object = request.__dict__.get(_USER_ATTR)
    if loaded is not None:
        return loaded
    user: object = request.__dict__.get("user")
    unevaluated = getattr(user, "_wrapped", None) is empty  # an unloaded SimpleLazyObject
    if unevaluated and _in_event_loop():
        return None
    if getattr(user, "is_authenticated", False) is True:
        return user
    return None


async def arequest_user(request: HttpRequest) -> object | None:
    """``request_user`` for async code: loads the session user with ``request.auser()``."""
    auth: object = getattr(request, "auth", None)
    if getattr(auth, "is_authenticated", False):
        return auth
    user: object = request.__dict__.get("user")
    if getattr(user, "_wrapped", None) is empty:
        auser: Callable[[], Awaitable[object]] | None = getattr(request, "auser", None)
        if auser is not None:
            user = await auser()
        else:  # Django < 5.0: load the lazy user in one thread hop
            user = await sync_to_async(_loaded)(user)
    if getattr(user, "is_authenticated", False) is True:
        # Later sync lookups in this request (querysets, services) see the loaded user.
        request.__dict__[_USER_ATTR] = user
        return user
    return None


def current_user(request: HttpRequest, user_type: type[UserT]) -> UserT:
    """The authenticated ``user_type`` instance; raises ``AuthenticationError`` (401) otherwise."""
    user = request_user(request)
    if not isinstance(user, user_type):
        raise AuthenticationError()
    return user


async def acurrent_user(request: HttpRequest, user_type: type[UserT]) -> UserT:
    user = await arequest_user(request)
    if not isinstance(user, user_type):
        raise AuthenticationError()
    return user


def authenticated_user(user_type: type[UserT]) -> Callable[[HttpRequest], UserT]:
    """A scoped factory: ``container.scoped(User, authenticated_user(User))``.

    :param user_type: The user class; the authenticated user must be one, else 401.
    """

    def factory(request: HttpRequest) -> UserT:
        return current_user(request, user_type)

    return factory


def aauthenticated_user(user_type: type[UserT]) -> Callable[[HttpRequest], Awaitable[UserT]]:
    """``authenticated_user`` for async containers and ``as_permission(asubject=...)``."""

    async def factory(request: HttpRequest) -> UserT:
        return await acurrent_user(request, user_type)

    return factory


@overload
def request_context(
    user_type: type[UserT], tenant: None = None
) -> Callable[[HttpRequest], RequestContext[UserT, None]]: ...


@overload
def request_context(
    user_type: type[UserT], tenant: Callable[[HttpRequest, UserT], TenantT]
) -> Callable[[HttpRequest], RequestContext[UserT, TenantT]]: ...


def request_context(
    user_type: type[UserT],
    tenant: Callable[[HttpRequest, UserT], TenantT] | None = None,
) -> (
    Callable[[HttpRequest], RequestContext[UserT, TenantT]]
    | Callable[[HttpRequest], RequestContext[UserT, None]]
):
    """A scoped factory building ``RequestContext`` from the request.

    ::

        container.scoped(RequestContext[User, None], request_context(User))
        container.scoped(RequestContext[User, Org], request_context(User, tenant=org_of))

    The request id comes from ``X-Request-ID`` and the trace id from ``traceparent``
    when present. In async operations the async container uses ``arequest_context``.

    :param user_type: The user class; the authenticated user must be one, else 401.
    :param tenant: Returns the tenant from ``(request, user)``; ``None`` for single-tenant apps.
    """
    if tenant is None:

        def untenanted(request: HttpRequest) -> RequestContext[UserT, None]:
            return _context(request, current_user(request, user_type), None)

        return untenanted
    tenant_of = tenant

    def factory(request: HttpRequest) -> RequestContext[UserT, TenantT]:
        user = current_user(request, user_type)
        return _context(request, user, tenant_of(request, user))

    return factory


@overload
def arequest_context(
    user_type: type[UserT], tenant: None = None
) -> Callable[[HttpRequest], Awaitable[RequestContext[UserT, None]]]: ...


@overload
def arequest_context(
    user_type: type[UserT], tenant: Callable[[HttpRequest, UserT], TenantT]
) -> Callable[[HttpRequest], Awaitable[RequestContext[UserT, TenantT]]]: ...


def arequest_context(
    user_type: type[UserT],
    tenant: Callable[[HttpRequest, UserT], TenantT] | None = None,
) -> (
    Callable[[HttpRequest], Awaitable[RequestContext[UserT, TenantT]]]
    | Callable[[HttpRequest], Awaitable[RequestContext[UserT, None]]]
):
    """``request_context`` for async operations (loads session users without blocking)."""
    if tenant is None:

        async def untenanted(request: HttpRequest) -> RequestContext[UserT, None]:
            return _context(request, await acurrent_user(request, user_type), None)

        return untenanted
    tenant_of = tenant

    async def factory(request: HttpRequest) -> RequestContext[UserT, TenantT]:
        user = await acurrent_user(request, user_type)
        return _context(request, user, tenant_of(request, user))

    return factory


def _context(request: HttpRequest, user: UserT, tenant: TenantT) -> RequestContext[UserT, TenantT]:
    request_id = request.headers.get("X-Request-ID")
    traceparent = request.headers.get("traceparent")
    trace_id = traceparent.split("-")[1] if traceparent and traceparent.count("-") >= 3 else None
    if request_id:
        return RequestContext(user=user, tenant=tenant, request_id=request_id, trace_id=trace_id)
    return RequestContext(user=user, tenant=tenant, trace_id=trace_id)


def _loaded(user: object) -> object:
    getattr(user, "is_authenticated", None)  # evaluates the SimpleLazyObject
    wrapped: object = getattr(user, "_wrapped", user)
    return wrapped


def _in_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True
