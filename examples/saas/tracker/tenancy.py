"""Tenant resolution, authentication and roles for the tracker."""

from django.contrib.auth.models import User
from django.http import HttpRequest
from ninja.security import HttpBearer
from ninja_devx import BasePermission, current_tenant

from tracker.models import Membership, Workspace


class DemoTokenAuth(HttpBearer):
    """``Authorization: Bearer <username>``. For the example only: use real tokens."""

    def authenticate(self, request: HttpRequest, token: str) -> User | None:
        return User.objects.filter(username=token).first()


def workspace_of(request: HttpRequest) -> Workspace | None:
    """The workspace named by ``X-Workspace``, if the caller is a member of it."""
    slug = request.headers.get("X-Workspace")
    user = getattr(request, "auth", None)
    if not slug or not isinstance(user, User):
        return None
    return Workspace.objects.filter(slug=slug, memberships__user=user).first()


def role_of(request: HttpRequest) -> str | None:
    user = getattr(request, "auth", None)
    workspace = current_tenant(request) or workspace_of(request)
    if not isinstance(user, User) or not isinstance(workspace, Workspace):
        return None
    membership = Membership.objects.filter(user=user, workspace=workspace).first()
    return membership.role if membership else None


class IsWorkspaceAdmin(BasePermission[object]):
    """The caller is an admin of the current workspace."""

    message = "Workspace admins only."

    def has_permission(self, request: HttpRequest, /) -> bool:
        return role_of(request) == Membership.Role.ADMIN
