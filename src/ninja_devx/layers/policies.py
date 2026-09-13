"""Policies: one authorization rule usable from services and (via permissions) HTTP."""

from __future__ import annotations

from typing import Protocol, TypeVar

from django.utils.translation import gettext_noop

from .errors import PermissionDenied

__all__ = ["Policy", "PolicyDenied", "allowed", "require"]

SubjectT_contra = TypeVar("SubjectT_contra", contravariant=True)
ObjT_contra = TypeVar("ObjT_contra", contravariant=True)
SubjectT = TypeVar("SubjectT")
ObjT = TypeVar("ObjT")


class Policy(Protocol[SubjectT_contra, ObjT_contra]):
    """``allows(subject, obj)``: the subject is usually a user or a ``RequestContext``."""

    def allows(self, subject: SubjectT_contra, obj: ObjT_contra, /) -> bool: ...


class PolicyDenied(PermissionDenied):
    """You do not have permission to perform this action."""

    default_message = gettext_noop("You do not have permission to perform this action.")

    code = "policy_denied"


def allowed(policy: Policy[SubjectT, ObjT], subject: SubjectT, obj: ObjT) -> bool:
    return policy.allows(subject, obj)


def require(policy: Policy[SubjectT, ObjT], subject: SubjectT, obj: ObjT) -> None:
    """Raise ``PolicyDenied`` (HTTP 403 when mapped) unless ``policy`` allows it."""
    if not policy.allows(subject, obj):
        message: object = getattr(policy, "message", "")
        raise PolicyDenied(message if isinstance(message, str) else "")
