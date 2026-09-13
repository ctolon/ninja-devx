"""Typed class-based controllers, permissions, CRUD and DI for Django Ninja.

Attributes are imported lazily (PEP 562), so ``import ninja_devx`` does not require
configured Django settings (the pytest plugin relies on this).
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, version
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from .dependencies.container import (
        AsyncRequestScopeProvider,
        AsyncResolver,
        Container,
        ContainerLike,
        Lifetime,
        RequestScopeProvider,
        Resolver,
    )
    from .dependencies.injection import Inject, Resolve, resolve
    from .dependencies.instances import Invocation, get_invocation
    from .exceptions import (
        AsyncLazyAccessError,
        BlockingCallWarning,
        CircularDependencyError,
        ControllerConfigError,
        DependencyResolutionError,
        MixedPathWarning,
        NinjaDevXError,
    )
    from .http.conditional import ETag, PreconditionFailed, PreconditionRequired, conditional
    from .http.errors import ErrorMap
    from .idempotency import idempotent
    from .routing.controller import Controller, ControllerOptions, Scope
    from .routing.hooks import (
        AsyncOperationHook,
        LoggingHook,
        OperationHook,
        OperationInfo,
        get_operation,
    )
    from .routing.mounting import Mount, mount
    from .routing.operations import (
        OperationOptions,
        OperationSpec,
        RouteOptions,
        api_operation,
        async_variant,
        delete,
        get,
        patch,
        post,
        put,
    )
    from .routing.plugins import ControllerPlugin
    from .routing.use_cases import use_case
    from .security.auth import (
        AuthedRequest,
        aauthenticated_user,
        acurrent_user,
        arequest_context,
        arequest_user,
        authenticated_user,
        current_user,
        request_context,
        request_user,
    )
    from .security.permissions import (
        AllowAny,
        Also,
        BasePermission,
        DenyAll,
        DjangoModelPermissions,
        HasDjangoPermission,
        IsAuthenticated,
        IsAuthenticatedOrReadOnly,
        IsOwner,
        IsReadOnly,
        IsStaff,
        IsSuperuser,
        as_permission,
    )
    from .security.tenancy import MissingTenant, current_tenant
    from .serialization.schemas import Input, Output, Patch, PatchData, ReadOnly, WriteOnly
    from .serialization.visibility import FieldVisibility, VisibleTo

_EXPORTS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "get_invocation": ".dependencies.instances",
        "Invocation": ".dependencies.instances",
        "aauthenticated_user": ".security.auth",
        "acurrent_user": ".security.auth",
        "arequest_context": ".security.auth",
        "arequest_user": ".security.auth",
        "AuthedRequest": ".security.auth",
        "authenticated_user": ".security.auth",
        "current_user": ".security.auth",
        "request_context": ".security.auth",
        "request_user": ".security.auth",
        "conditional": ".http.conditional",
        "ETag": ".http.conditional",
        "PreconditionFailed": ".http.conditional",
        "PreconditionRequired": ".http.conditional",
        "Controller": ".routing.controller",
        "ControllerOptions": ".routing.controller",
        "Scope": ".routing.controller",
        "AsyncRequestScopeProvider": ".dependencies.container",
        "AsyncResolver": ".dependencies.container",
        "Container": ".dependencies.container",
        "ContainerLike": ".dependencies.container",
        "Lifetime": ".dependencies.container",
        "RequestScopeProvider": ".dependencies.container",
        "Resolver": ".dependencies.container",
        "ErrorMap": ".http.errors",
        "AsyncLazyAccessError": ".exceptions",
        "BlockingCallWarning": ".exceptions",
        "CircularDependencyError": ".exceptions",
        "ControllerConfigError": ".exceptions",
        "DependencyResolutionError": ".exceptions",
        "MixedPathWarning": ".exceptions",
        "NinjaDevXError": ".exceptions",
        "AsyncOperationHook": ".routing.hooks",
        "get_operation": ".routing.hooks",
        "LoggingHook": ".routing.hooks",
        "OperationHook": ".routing.hooks",
        "OperationInfo": ".routing.hooks",
        "idempotent": ".idempotency",
        "Inject": ".dependencies.injection",
        "Resolve": ".dependencies.injection",
        "resolve": ".dependencies.injection",
        "Mount": ".routing.mounting",
        "mount": ".routing.mounting",
        "api_operation": ".routing.operations",
        "async_variant": ".routing.operations",
        "delete": ".routing.operations",
        "get": ".routing.operations",
        "OperationOptions": ".routing.operations",
        "OperationSpec": ".routing.operations",
        "patch": ".routing.operations",
        "post": ".routing.operations",
        "put": ".routing.operations",
        "RouteOptions": ".routing.operations",
        "AllowAny": ".security.permissions",
        "Also": ".security.permissions",
        "as_permission": ".security.permissions",
        "BasePermission": ".security.permissions",
        "DenyAll": ".security.permissions",
        "DjangoModelPermissions": ".security.permissions",
        "HasDjangoPermission": ".security.permissions",
        "IsAuthenticated": ".security.permissions",
        "IsAuthenticatedOrReadOnly": ".security.permissions",
        "IsOwner": ".security.permissions",
        "IsReadOnly": ".security.permissions",
        "IsStaff": ".security.permissions",
        "IsSuperuser": ".security.permissions",
        "ControllerPlugin": ".routing.plugins",
        "Input": ".serialization.schemas",
        "Output": ".serialization.schemas",
        "Patch": ".serialization.schemas",
        "PatchData": ".serialization.schemas",
        "ReadOnly": ".serialization.schemas",
        "WriteOnly": ".serialization.schemas",
        "current_tenant": ".security.tenancy",
        "MissingTenant": ".security.tenancy",
        "use_case": ".routing.use_cases",
        "FieldVisibility": ".serialization.visibility",
        "VisibleTo": ".serialization.visibility",
    }
)

__all__ = [
    "AllowAny",
    "Also",
    "AsyncLazyAccessError",
    "AsyncOperationHook",
    "AsyncRequestScopeProvider",
    "AsyncResolver",
    "AuthedRequest",
    "BasePermission",
    "BlockingCallWarning",
    "CircularDependencyError",
    "Container",
    "ContainerLike",
    "Controller",
    "ControllerConfigError",
    "ControllerOptions",
    "ControllerPlugin",
    "DenyAll",
    "DependencyResolutionError",
    "DjangoModelPermissions",
    "ETag",
    "ErrorMap",
    "FieldVisibility",
    "HasDjangoPermission",
    "Inject",
    "Input",
    "Invocation",
    "IsAuthenticated",
    "IsAuthenticatedOrReadOnly",
    "IsOwner",
    "IsReadOnly",
    "IsStaff",
    "IsSuperuser",
    "Lifetime",
    "LoggingHook",
    "MissingTenant",
    "MixedPathWarning",
    "Mount",
    "NinjaDevXError",
    "OperationHook",
    "OperationInfo",
    "OperationOptions",
    "OperationSpec",
    "Output",
    "Patch",
    "PatchData",
    "PreconditionFailed",
    "PreconditionRequired",
    "ReadOnly",
    "RequestScopeProvider",
    "Resolve",
    "Resolver",
    "RouteOptions",
    "Scope",
    "VisibleTo",
    "WriteOnly",
    "aauthenticated_user",
    "acurrent_user",
    "api_operation",
    "arequest_context",
    "arequest_user",
    "as_permission",
    "async_variant",
    "authenticated_user",
    "conditional",
    "current_tenant",
    "current_user",
    "delete",
    "get",
    "get_invocation",
    "get_operation",
    "idempotent",
    "mount",
    "patch",
    "post",
    "put",
    "request_context",
    "request_user",
    "resolve",
    "use_case",
]

try:
    __version__ = version("ninja-devx")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
    __version__ = "0.0.1"


def __getattr__(name: str) -> object:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value: object = getattr(importlib.import_module(module, __name__), name)
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
