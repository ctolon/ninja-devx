from collections.abc import Mapping

from django.contrib.auth.models import User
from django.http import HttpRequest
from ninja.errors import HttpError
from ninja_devx import ControllerOptions, current_user
from ninja_devx.contrib.audit.api import AuditHistoryMixin
from ninja_devx.contrib.audit.log import AuditMixin
from ninja_devx.crud import (
    CRUDController,
    CursorPagination,
    ObjectSharingMixin,
    Parent,
    SoftDelete,
    SoftDeleteMixin,
)
from ninja_devx.crud.transfer import ExportMixin
from ninja_devx.http.conditional import ETag
from ninja_devx.http.throttling import ClientRateThrottle, ScopedRateThrottle
from ninja_devx.layers import Conflict, ModelService, dual
from ninja_devx.security.object_permissions import ObjectPermissions, assign_perm
from ninja_devx.security.tenancy import current_tenant

from tracker.models import Document, Issue, Membership, Project, Workspace
from tracker.schemas import (
    DocumentIn,
    DocumentOut,
    IssueIn,
    IssueOut,
    ProjectIn,
    ProjectOut,
)
from tracker.tenancy import DemoTokenAuth, IsWorkspaceAdmin


class TrackerOptions:
    """Conventions shared by every controller of the tracker."""

    options = ControllerOptions(
        auth=DemoTokenAuth(),
        throttle=[ClientRateThrottle(user="600/min", anon="30/min")],
    )


class ProjectController(
    SoftDeleteMixin[Project, ProjectOut], CRUDController[Project, ProjectOut, ProjectIn]
):
    """Projects of the current workspace. Deleting archives; admins can unarchive."""

    options = TrackerOptions.options
    tenant_field = "workspace"
    soft_delete = SoftDelete("archived_at", deleted_by="archived_by")
    etag = ETag(field="updated_at")
    ordering_fields = ("name", "updated_at")
    routes = {
        "restore": {"path": "/{pk}/unarchive", "permissions": [IsWorkspaceAdmin()]},
        "destroy": {"permissions": [IsWorkspaceAdmin()]},
    }


class IssueClosed(Conflict):
    """Closed issues cannot be edited."""

    code = "issue_closed"


class IssueService(ModelService[Issue]):
    @dual
    def update(self, instance: Issue, data: Mapping[str, object]) -> Issue:
        if instance.status == Issue.Status.CLOSED and data.get("status") != Issue.Status.OPEN:
            raise IssueClosed()
        return super().update(instance, data)


class IssueController(
    AuditHistoryMixin[Issue],
    AuditMixin[Issue],
    ExportMixin[Issue, IssueOut],
    CRUDController[Issue, IssueOut, IssueIn],
):
    """Issues of a project, mounted under ``/projects/{project_pk}/issues``.

    Every change is audited (``GET /{pk}/history``); ``GET /export?format=csv`` exports
    the filtered list with the same visibility rules.
    """

    options = TrackerOptions.options
    tenant_field = "project__workspace"
    parent = Parent(Project, field="project", tenant_field="workspace")
    service_class = IssueService
    etag = ETag(field="updated_at", require_if_match=True)
    filter_fields = {"status": ("exact",), "priority": ("gte", "lte")}
    search_fields = ("title",)
    ordering_fields = ("created", "priority")
    default_ordering = ("-created",)
    pagination_class = CursorPagination
    pagination_options = {"page_size": 20}
    routes = {
        "create": {"throttle": [ScopedRateThrottle("issue-writes")]},
        "history": {"permissions": [IsWorkspaceAdmin()]},
    }

    def audit_metadata(self, request: HttpRequest) -> Mapping[str, object]:
        workspace = current_tenant(request)
        return {"workspace": workspace.slug if isinstance(workspace, Workspace) else None}


class DocumentController(
    ObjectSharingMixin[Document], CRUDController[Document, DocumentOut, DocumentIn]
):
    """Private documents: the author gets every permission and shares with members.

    ``GET /documents/`` lists only documents the caller may view (one SQL query);
    ``PUT /documents/{pk}/permissions`` shares, e.g. ``{"user_id": 7, "permissions": ["view"]}``.
    """

    options = TrackerOptions.options
    tenant_field = "workspace"
    object_permissions = ObjectPermissions()
    shareable_permissions = ("view", "change")

    def perform_create(self, request: HttpRequest, payload: DocumentIn) -> Document:
        author = current_user(request, User)
        data = {**payload.model_dump(), **self.context_data(request), "owner": author}
        document = self.get_service(request).create(data)
        for action in ("add", "view", "change", "delete"):
            assign_perm(f"tracker.{action}_document", author, document)
        return document

    def validate_holder(self, request: HttpRequest, obj: Document, holder: object) -> None:
        if not isinstance(holder, User):  # groups: any group of the workspace would do
            return
        if not Membership.objects.filter(workspace=obj.workspace, user=holder).exists():
            raise HttpError(422, "Documents can only be shared with workspace members.")
