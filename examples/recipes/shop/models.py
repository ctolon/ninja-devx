"""The shared domain: every recipe implements the same order API over these models."""

from django.conf import settings
from django.db import models


class Product(models.Model):
    name = models.CharField(max_length=100)
    stock = models.PositiveIntegerField(default=0)

    def __str__(self) -> str:
        return self.name


class Order(models.Model):
    class Status(models.TextChoices):
        PLACED = "placed"
        CANCELLED = "cancelled"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PLACED)

    class Meta:
        ordering = ("id",)

    def __str__(self) -> str:
        return f"{self.quantity} x {self.product_id}"
