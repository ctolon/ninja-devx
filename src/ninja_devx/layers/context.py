"""Who is acting, for which tenant, in which request: passed to services without HTTP."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Generic, TypeVar
from uuid import uuid4

__all__ = ["RequestContext"]

UserT = TypeVar("UserT")
TenantT = TypeVar("TenantT")


@dataclass(frozen=True, slots=True)
class RequestContext(Generic[UserT, TenantT]):
    """The acting user and tenant plus correlation ids.

    Register a factory for the concrete key and inject it where needed::

        container.scoped(RequestContext[User, None], request_context(User))

    Outside HTTP (tasks, commands, tests) build one directly and open a scope::

        with container.scope({RequestContext[User, None]: RequestContext(user, None)}) as scope:
            scope.resolve(OrderService).place(...)
    """

    user: UserT
    """The acting user."""
    tenant: TenantT
    """The acting tenant (``None`` for single-tenant apps)."""
    request_id: str = field(default_factory=lambda: uuid4().hex)
    """Correlation id (``X-Request-ID`` or generated)."""
    trace_id: str | None = None
    """W3C trace id from ``traceparent``, when present."""
    metadata: Mapping[str, str] = field(default_factory=dict[str, str])
    """Free-form string metadata for logs and audit."""
