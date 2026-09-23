"""django-rules predicates and permission rules as ninja-devx permissions.

Needs ``pip install ninja-devx[rules]``::

    @rules.predicate
    def is_author(user, post):
        return post is None or post.author_id == user.pk

    rules.add_perm("blog.change_post", is_author | rules.is_staff)

    class PostController(CRUDController[Post, PostOut, PostIn]):
        options = ControllerOptions(permissions=[IsAuthenticated(), HasRule("blog.change_post")])

    @get("/{pk}/draft", permissions=Also(HasRule(is_author)))

A rule is tested with the user before the operation, and with the user and the object
when the operation loads one (``get_object``, ``Instance[...]``), like
``user.has_perm(name)`` and ``user.has_perm(name, obj)`` with rules' authentication
backend. Before an object is loaded two-argument predicates receive ``None`` for it, as
everywhere in rules, and list operations never load one. Anonymous callers are tested as
``AnonymousUser``. Predicates are sync; async operations run them in a thread.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest
from rules import permissions as rule_permissions
from rules.predicates import Predicate

from ..security.auth import arequest_user, request_user
from ..security.permissions import BasePermission

__all__ = ["HasRule"]


@dataclass(frozen=True, slots=True)
class HasRule(BasePermission[object]):
    """Requires a rule: ``HasRule("blog.change_post")`` or ``HasRule(is_author)``."""

    rule: str | Predicate | Callable[..., object]
    """A name in rules' permission set (``"app.codename"``) or a predicate (any callable
    taking ``(user)`` or ``(user, obj)``)."""

    def __post_init__(self) -> None:
        if not isinstance(self.rule, str | Predicate):
            object.__setattr__(self, "rule", Predicate(self.rule))

    def has_permission(self, request: HttpRequest, /) -> bool:
        return self._test(request_user(request))

    async def ahas_permission(self, request: HttpRequest, /) -> bool:
        return bool(await sync_to_async(self._test)(await arequest_user(request)))

    def has_object_permission(self, request: HttpRequest, obj: object, /) -> bool:
        return self._test(request_user(request), obj)

    async def ahas_object_permission(self, request: HttpRequest, obj: object, /) -> bool:
        return bool(await sync_to_async(self._test)(await arequest_user(request), obj))

    def _test(self, user: object | None, *target: object) -> bool:
        subject = AnonymousUser() if user is None else user
        if isinstance(self.rule, str):
            has_perm: Callable[..., object] = getattr(rule_permissions, "has_perm")  # noqa: B009 - untyped
            return bool(has_perm(self.rule, subject, *target))
        predicate = cast("Predicate", self.rule)  # __post_init__ wrapped plain callables
        return bool(predicate.test(subject, *target))
