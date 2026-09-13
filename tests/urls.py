from django.contrib import admin
from django.urls import path
from ninja import NinjaAPI

from ninja_devx import mount
from tests.testapp.api import ArticleController, PingController

api = NinjaAPI(title="Test API", version="1.0.0", urls_namespace="test-api")
mount(api, {"/articles": ArticleController, "/ping": PingController})

urlpatterns = [path("api/", api.urls), path("admin/", admin.site.urls)]
