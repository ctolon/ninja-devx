from django.conf import settings
from django.db import models

from ninja_devx.models import SoftDeletable, Stamped


class Tag(models.Model):
    name = models.CharField(max_length=50, unique=True)


class Article(models.Model):
    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    body = models.TextField(blank=True)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    tags = models.ManyToManyField(Tag, blank=True)
    published = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("id",)


class Comment(models.Model):
    article = models.ForeignKey(Article, on_delete=models.CASCADE, related_name="comments")
    body = models.TextField()

    class Meta:
        ordering = ("id",)


class Note(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        DONE = "done", "Done"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    text = models.CharField(max_length=100)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    priority = models.IntegerField(default=0)
    archived = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("id",)


class Organization(models.Model):
    name = models.CharField(max_length=50)


class Project(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    name = models.CharField(max_length=50)
    is_active = models.BooleanField(default=True)
    status = models.CharField(max_length=10, default="live")
    removed_at = models.DateTimeField(null=True, blank=True)
    removed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )

    class Meta:
        ordering = ("id",)


class Task(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="tasks")
    title = models.CharField(max_length=50)
    slug = models.SlugField(unique=True, null=True, blank=True)

    class Meta:
        ordering = ("id",)


class Document(Stamped, SoftDeletable):
    title = models.CharField(max_length=100)

    class Meta:
        ordering = ("id",)
