from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path
from ninja import NinjaAPI
from ninja.security import django_auth
from ninja_devx import Container, mount

from blog.api.comments import CommentController
from blog.api.posts import PostController
from blog.api.tags import TagController

container = Container()

api = NinjaAPI(title="Blog API", version="1.0.0", auth=django_auth)
mount(
    api,
    {
        "/posts": PostController,
        "/posts/{post_pk}/comments": CommentController,
        "/tags": TagController,
    },
    prefix="/v1",
    container=container,
)

urlpatterns = [
    path("api/", api.urls),
    path("accounts/login/", LoginView.as_view(), name="login"),
    path("accounts/logout/", LogoutView.as_view(), name="logout"),
]
