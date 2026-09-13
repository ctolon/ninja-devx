from django.apps import AppConfig


class WebhooksConfig(AppConfig):
    name = "ninja_devx.contrib.webhooks"
    label = "ninja_devx_webhooks"
    verbose_name = "Webhooks"
    default_auto_field = "django.db.models.BigAutoField"
