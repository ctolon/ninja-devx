from django.urls import path
from ninja import NinjaAPI
from ninja_devx import mount
from ninja_devx.http.health import CacheCheck, DatabaseCheck, HealthController
from ninja_devx.http.middleware import RequestIDMiddleware, ServerTimingMiddleware, use_middleware

from tracker.api import DocumentController, IssueController, ProjectController


class Health(HealthController):
    health_checks = (DatabaseCheck(), CacheCheck())


api = NinjaAPI(title="Tracker", version="1.0.0")
use_middleware(api, RequestIDMiddleware(), ServerTimingMiddleware())
mount(api, {"/health": Health})
mount(
    api,
    {
        "/projects": ProjectController,
        "/projects/{project_pk}/issues": IssueController,
        "/documents": DocumentController,
    },
    prefix="/v1",
)

urlpatterns = [path("api/", api.urls)]
