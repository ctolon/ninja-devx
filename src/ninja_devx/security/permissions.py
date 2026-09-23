"""Typed, composable permissions evaluated after Django Ninja has validated the request.

Authentication stays Ninja's job (``auth=``); permissions decide what an
authenticated (or anonymous) caller may do::

    @get("/{pk}", auth=django_auth, permissions=[IsAuthenticated() & IsStaff()])

Every permission works in sync and async operations: async operations call
``ahas_permission``/``ahas_object_permission``, which default to the sync checks and are
overridden by the built-ins to load session users without blocking.

Permissions are immutable and shared between requests, so they must not store state.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import ClassVar, Generic, Literal, Never, TypeAlias, TypeVar, cast

from asgiref.sync import sync_to_async
from django.db.models import Model
from django.http import HttpRequest
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop
from ninja.errors import HttpError

from .._permission_eval import denied
from .._permission_eval_async import adenied
from ..layers.policies import Policy
from ..routing.hooks import get_operation
from .auth import arequest_user, request_user

__all__ = [
    "AllOf",
    "AllowAny",
    "Also",
    "AnyOf",
    "BasePermission",
    "DenyAll",
    "DjangoModelPermissions",
    "HasDjangoPermission",
    "IsAuthenticated",
    "IsAuthenticatedOrReadOnly",
    "IsOwner",
    "IsReadOnly",
    "IsStaff",
    "IsSuperuser",
    "Not",
    "PermissionResult",
    "PolicyPermission",
    "acheck_object_permissions",
    "as_permission",
    "check_object_permissions",
]

ObjT_contra = TypeVar("ObjT_contra", contravariant=True)
ObjT = TypeVar("ObjT")
SubjectT = TypeVar("SubjectT")

PermissionResult: TypeAlias = bool | Awaitable[bool]
"""Sync permissions return ``bool``; a coroutine ``has_permission`` makes it async-only."""

AnyPermission: TypeAlias = "BasePermission[Never]"
"""Any permission, whatever object type it checks (permissions are contravariant)."""

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "QUERY"})
_REQUEST_ATTR = "_ninja_devx_permissions"


class BasePermission(Generic[ObjT_contra]):
    """Allow everything by default; override the checks you need.

    ``has_permission`` runs before the operation. ``has_object_permission`` runs when
    the operation loads an object (``get_object``, ``Instance[...]``) or calls
    ``check_object_permissions``. Override the ``a``-prefixed versions for native async
    checks. Combine with ``&``, ``|`` and ``~``.
    """

    message: str = gettext_noop("You do not have permission to perform this action.")
    """``detail`` of the denial response, translated with ``gettext`` when sent (so your
    own messages can come from your project's catalog)."""
    status_code: int = 403
    """Status of the denial response (403, or 401 for authentication)."""
    combinator: ClassVar[Literal["all", "any", "not"] | None] = None
    """Set by ``AllOf``/``AnyOf``/``Not``, which evaluate ``operands`` instead of checks."""

    @property
    def operands(self) -> tuple[BasePermission[ObjT_contra], ...]:
        return ()

    def has_permission(self, request: HttpRequest, /) -> PermissionResult:
        return True

    def has_object_permission(self, request: HttpRequest, obj: ObjT_contra, /) -> PermissionResult:
        return True

    async def ahas_permission(self, request: HttpRequest, /) -> bool:
        return await _awaited(self.has_permission(request))

    async def ahas_object_permission(self, request: HttpRequest, obj: ObjT_contra, /) -> bool:
        return await _awaited(self.has_object_permission(request, obj))

    def __and__(self, other: BasePermission[ObjT_contra]) -> AllOf[ObjT_contra]:
        return AllOf((self, other))

    def __or__(self, other: BasePermission[ObjT_contra]) -> AnyOf[ObjT_contra]:
        return AnyOf((self, other))

    def __invert__(self) -> Not[ObjT_contra]:
        return Not(self)


class Also(tuple["BasePermission[Never]", ...]):
    """Operation permissions added to the controller's instead of replacing them.

    ``@get("/{pk}", permissions=Also(IsOwner()))``
    """

    __slots__ = ()

    def __new__(cls, *permissions: BasePermission[Never]) -> Also:
        return super().__new__(cls, permissions)


async def _awaited(result: PermissionResult) -> bool:
    return await result if inspect.isawaitable(result) else result


# --- Combinators ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AllOf(BasePermission[ObjT_contra]):
    """Allowed when every permission allows; reports the first denial."""

    permissions: tuple[BasePermission[ObjT_contra], ...]
    combinator: ClassVar[Literal["all", "any", "not"] | None] = "all"

    @property
    def operands(self) -> tuple[BasePermission[ObjT_contra], ...]:
        return self.permissions

    def has_permission(self, request: HttpRequest, /) -> PermissionResult:
        return denied(self, request, None) is None

    def has_object_permission(self, request: HttpRequest, obj: ObjT_contra, /) -> PermissionResult:
        return denied(self, request, (obj,)) is None

    async def ahas_permission(self, request: HttpRequest, /) -> bool:
        return await adenied(self, request, None) is None

    async def ahas_object_permission(self, request: HttpRequest, obj: ObjT_contra, /) -> bool:
        return await adenied(self, request, (obj,)) is None


@dataclass(frozen=True, slots=True)
class AnyOf(BasePermission[ObjT_contra]):
    """Allowed when at least one permission allows; reports the last denial."""

    permissions: tuple[BasePermission[ObjT_contra], ...]
    combinator: ClassVar[Literal["all", "any", "not"] | None] = "any"

    @property
    def operands(self) -> tuple[BasePermission[ObjT_contra], ...]:
        return self.permissions

    def has_permission(self, request: HttpRequest, /) -> PermissionResult:
        return denied(self, request, None) is None

    def has_object_permission(self, request: HttpRequest, obj: ObjT_contra, /) -> PermissionResult:
        return denied(self, request, (obj,)) is None

    async def ahas_permission(self, request: HttpRequest, /) -> bool:
        return await adenied(self, request, None) is None

    async def ahas_object_permission(self, request: HttpRequest, obj: ObjT_contra, /) -> bool:
        return await adenied(self, request, (obj,)) is None


@dataclass(frozen=True, slots=True)
class Not(BasePermission[ObjT_contra]):
    """Inverts a permission. Uses its own ``message``/``status_code`` when denying."""

    permission: BasePermission[ObjT_contra]
    combinator: ClassVar[Literal["all", "any", "not"] | None] = "not"

    @property
    def operands(self) -> tuple[BasePermission[ObjT_contra], ...]:
        return (self.permission,)

    def has_permission(self, request: HttpRequest, /) -> PermissionResult:
        return denied(self, request, None) is None

    def has_object_permission(self, request: HttpRequest, obj: ObjT_contra, /) -> PermissionResult:
        return denied(self, request, (obj,)) is None

    async def ahas_permission(self, request: HttpRequest, /) -> bool:
        return await adenied(self, request, None) is None

    async def ahas_object_permission(self, request: HttpRequest, obj: ObjT_contra, /) -> bool:
        return await adenied(self, request, (obj,)) is None


# --- Built-in permissions ------------------------------------------------------


class AllowAny(BasePermission[object]):
    """Allows every request."""


class DenyAll(BasePermission[object]):
    """Denies every request (403)."""

    def has_permission(self, request: HttpRequest, /) -> bool:
        return False


class IsAuthenticated(BasePermission[object]):
    """Requires Ninja authentication (``request.auth``) or an authenticated ``request.user``."""

    message = gettext_noop("Authentication credentials were not provided.")
    status_code = 401

    def has_permission(self, request: HttpRequest, /) -> bool:
        return getattr(request, "auth", None) is not None or request_user(request) is not None

    async def ahas_permission(self, request: HttpRequest, /) -> bool:
        if getattr(request, "auth", None) is not None:
            return True
        return await arequest_user(request) is not None


class IsAuthenticatedOrReadOnly(IsAuthenticated):
    """Safe methods (GET, HEAD, OPTIONS) for everyone; other methods need authentication."""

    def has_permission(self, request: HttpRequest, /) -> bool:
        return request.method in SAFE_METHODS or super().has_permission(request)

    async def ahas_permission(self, request: HttpRequest, /) -> bool:
        return request.method in SAFE_METHODS or await super().ahas_permission(request)


class IsReadOnly(BasePermission[object]):
    """Allows safe methods only; combine it: ``[IsAuthenticated(), IsReadOnly() | IsStaff()]``."""

    message = gettext_noop("This action is read-only.")

    def has_permission(self, request: HttpRequest, /) -> bool:
        return request.method in SAFE_METHODS


class IsStaff(BasePermission[object]):
    """The user has ``is_staff``."""

    def has_permission(self, request: HttpRequest, /) -> bool:
        return bool(getattr(request_user(request), "is_staff", False))

    async def ahas_permission(self, request: HttpRequest, /) -> bool:
        return bool(getattr(await arequest_user(request), "is_staff", False))


class IsSuperuser(BasePermission[object]):
    """The user has ``is_superuser``."""

    def has_permission(self, request: HttpRequest, /) -> bool:
        return bool(getattr(request_user(request), "is_superuser", False))

    async def ahas_permission(self, request: HttpRequest, /) -> bool:
        return bool(getattr(await arequest_user(request), "is_superuser", False))


@dataclass(frozen=True, slots=True)
class HasDjangoPermission(BasePermission[object]):
    """Requires Django model permissions, e.g. ``HasDjangoPermission("blog.change_post")``."""

    perms: tuple[str, ...]
    """Django permission names (``"app.change_model"``), all required."""

    def __init__(self, *perms: str) -> None:
        object.__setattr__(self, "perms", perms)

    def has_permission(self, request: HttpRequest, /) -> bool:
        return _has_perms(request_user(request), self.perms)

    async def ahas_permission(self, request: HttpRequest, /) -> bool:
        return await _ahas_perms(await arequest_user(request), self.perms)


_DEFAULT_PERMS_MAP: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "GET": ("view",),
        "HEAD": ("view",),
        "QUERY": ("view",),
        "OPTIONS": (),
        "POST": ("add",),
        "PUT": ("change",),
        "PATCH": ("change",),
        "DELETE": ("delete",),
    }
)


@dataclass(frozen=True, slots=True)
class DjangoModelPermissions(BasePermission[object]):
    """Maps the HTTP method to the model's ``view/add/change/delete`` permissions.

    The model is the controller's (``ModelController``) unless given explicitly.
    Unknown HTTP methods are denied.
    """

    model: type[Model] | None = None
    perms_map: Mapping[str, tuple[str, ...]] = field(default_factory=lambda: _DEFAULT_PERMS_MAP)

    def _codenames(self, request: HttpRequest) -> list[str] | None:
        actions = self.perms_map.get(request.method or "")
        if actions is None:
            return None
        options = (self.model or _operation_model(request))._meta
        return [f"{options.app_label}.{action}_{options.model_name}" for action in actions]

    def has_permission(self, request: HttpRequest, /) -> bool:
        codenames = self._codenames(request)
        return codenames is not None and _has_perms(request_user(request), codenames)

    async def ahas_permission(self, request: HttpRequest, /) -> bool:
        codenames = self._codenames(request)
        return codenames is not None and await _ahas_perms(await arequest_user(request), codenames)


@dataclass(frozen=True, slots=True)
class IsOwner(BasePermission[object]):
    """Object-level: ``obj.<field>`` must be the current user.

    ``field`` may follow relations: ``IsOwner("author")``, ``IsOwner("customer__user")``.
    ``ModelController`` selects the related objects so no extra query runs.
    """

    field: str = "owner"

    def has_object_permission(self, request: HttpRequest, obj: object, /) -> bool:
        return _owns(request_user(request), obj, self.field)

    async def ahas_object_permission(self, request: HttpRequest, obj: object, /) -> bool:
        return _owns(await arequest_user(request), obj, self.field)


@dataclass(frozen=True, slots=True)
class PolicyPermission(BasePermission[ObjT], Generic[SubjectT, ObjT]):
    """A ``Policy`` enforced on objects loaded by an operation (see ``as_permission``)."""

    policy: Policy[SubjectT, ObjT]
    subject: Callable[[HttpRequest], SubjectT]
    asubject: Callable[[HttpRequest], Awaitable[SubjectT]] | None = None

    def has_object_permission(self, request: HttpRequest, obj: ObjT, /) -> bool:
        return self.policy.allows(self.subject(request), obj)

    async def ahas_object_permission(self, request: HttpRequest, obj: ObjT, /) -> bool:
        subject = await self.asubject(request) if self.asubject else self.subject(request)
        return self.policy.allows(subject, obj)


def as_permission(
    policy: Policy[SubjectT, ObjT],
    subject: type[SubjectT] | Callable[[HttpRequest], SubjectT],
    *,
    asubject: Callable[[HttpRequest], Awaitable[SubjectT]] | None = None,
) -> PolicyPermission[SubjectT, ObjT]:
    """Use a service-layer ``Policy`` as an object permission.

    ``subject`` is the user class (the authenticated user must be one, else 401) or a
    function of the request::

        permissions=[as_permission(CanEdit(), User)]
        permissions=[as_permission(CanEdit(), membership_of, asubject=amembership_of)]

    :param policy: An object with ``allows(subject, obj) -> bool``.
    :param subject: The user class (the authenticated user must be one, else 401), or a function of
        the request.
    :param asubject: Async version of ``subject`` for async operations.
    """
    if isinstance(subject, type):
        from .auth import aauthenticated_user, authenticated_user

        user_type = cast("type[SubjectT]", subject)
        return PolicyPermission(
            policy, authenticated_user(user_type), asubject or aauthenticated_user(user_type)
        )
    return PolicyPermission(policy, subject, asubject)


def _owns(user: object, obj: object, path: str) -> bool:
    user_pk: object = getattr(user, "pk", None)
    if user_pk is None:
        return False
    *relations, last = path.split("__")
    target: object = obj
    for part in relations:
        target = getattr(target, part, None)
        if target is None:
            return False
    owner_pk: object = getattr(target, f"{last}_id", _MISSING)
    if owner_pk is _MISSING:
        owner_pk = getattr(getattr(target, last, None), "pk", None)
    return bool(owner_pk == user_pk)


def _has_perms(user: object, perms: Sequence[str]) -> bool:
    has_perms: Callable[[Sequence[str]], bool] | None = getattr(user, "has_perms", None)
    return has_perms is not None and has_perms(perms)


async def _ahas_perms(user: object, perms: Sequence[str]) -> bool:
    ahas_perms: Callable[[Sequence[str]], Awaitable[bool]] | None = getattr(
        user, "ahas_perms", None
    )
    if ahas_perms is not None:  # Django 5.2+
        return await ahas_perms(perms)
    return bool(await sync_to_async(_has_perms)(user, perms))


def _operation_model(request: HttpRequest) -> type[Model]:
    operation = get_operation(request)
    get_model: Callable[[], type[Model]] | None = getattr(
        operation and operation.controller, "get_model", None
    )
    if get_model is None:
        raise TypeError("DjangoModelPermissions needs a ModelController or an explicit model")
    return get_model()


_MISSING = object()


# --- Evaluation ----------------------------------------------------------------


def requires_async(permission: AnyPermission) -> bool:
    """Whether ``permission`` can only be evaluated asynchronously (coroutine checks)."""
    if permission.combinator is not None:
        return any(requires_async(child) for child in permission.operands)
    permission_type = type(permission)
    return inspect.iscoroutinefunction(
        permission_type.has_permission
    ) or inspect.iscoroutinefunction(permission_type.has_object_permission)


def _raise_denied(permission: AnyPermission) -> Never:
    raise HttpError(permission.status_code, _(permission.message))


def check_permissions(request: HttpRequest, permissions: Sequence[AnyPermission]) -> None:
    for permission in permissions:
        if (refused := denied(permission, request, None)) is not None:
            _raise_denied(refused)


async def acheck_permissions(request: HttpRequest, permissions: Sequence[AnyPermission]) -> None:
    for permission in permissions:
        if (refused := await adenied(permission, request, None)) is not None:
            _raise_denied(refused)


def bind_permissions(request: HttpRequest, permissions: Sequence[AnyPermission]) -> None:
    """Remember the operation's permissions for later object-level checks."""
    setattr(request, _REQUEST_ATTR, tuple(permissions))


def _bound_permissions(request: HttpRequest) -> tuple[AnyPermission, ...]:
    permissions: tuple[AnyPermission, ...] = getattr(request, _REQUEST_ATTR, ())
    return permissions


def check_object_permissions(request: HttpRequest, obj: object) -> None:
    """Raise ``HttpError`` unless the current operation's permissions allow ``obj``."""
    for permission in _bound_permissions(request):
        if (refused := denied(permission, request, (obj,))) is not None:
            _raise_denied(refused)


async def acheck_object_permissions(request: HttpRequest, obj: object) -> None:
    for permission in _bound_permissions(request):
        if (refused := await adenied(permission, request, (obj,))) is not None:
            _raise_denied(refused)
