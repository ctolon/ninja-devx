import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models


class UUIDUser(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
