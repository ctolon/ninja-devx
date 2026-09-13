"""HTTP-free building blocks for layered applications (services, repositories, policies...).

Nothing here imports Django Ninja: the same services run from views, tasks, commands
and tests. The web layer maps ``DomainError`` subclasses to responses.
"""

from .context import RequestContext
from .dual import BoundDual, dual
from .errors import (
    Conflict,
    DomainError,
    HttpMappable,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from .persistence import save_instance, validation_failed
from .policies import Policy, PolicyDenied, allowed, require
from .repository import AsyncRepository, ModelRepository, Repository
from .selectors import Selector
from .services import ModelService
from .tasks import (
    Enqueueable,
    ImmediateTaskQueue,
    OnCommitTaskQueue,
    RecordingTaskQueue,
    TaskQueue,
    after_commit,
)

__all__ = [
    "AsyncRepository",
    "BoundDual",
    "Conflict",
    "DomainError",
    "Enqueueable",
    "HttpMappable",
    "ImmediateTaskQueue",
    "ModelRepository",
    "ModelService",
    "NotFound",
    "OnCommitTaskQueue",
    "PermissionDenied",
    "Policy",
    "PolicyDenied",
    "RecordingTaskQueue",
    "Repository",
    "RequestContext",
    "Selector",
    "TaskQueue",
    "ValidationFailed",
    "after_commit",
    "allowed",
    "dual",
    "require",
    "save_instance",
    "validation_failed",
]
