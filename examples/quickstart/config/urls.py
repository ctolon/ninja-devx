from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path
from ninja import NinjaAPI
from ninja.security import django_auth
from ninja_devx import mount

from notes.api import HealthController, NoteController

api = NinjaAPI(title="Notes", version="1.0.0", auth=django_auth)
mount(api, {"/notes": NoteController, "/health": HealthController})

urlpatterns = [
    path("api/", api.urls),
    path("accounts/login/", LoginView.as_view(), name="login"),
    path("accounts/logout/", LogoutView.as_view(), name="logout"),
]
