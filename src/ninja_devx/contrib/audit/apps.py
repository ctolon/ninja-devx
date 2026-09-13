from django.apps import AppConfig


class AuditConfig(AppConfig):
    name = "ninja_devx.contrib.audit"
    label = "ninja_devx_audit"
    verbose_name = "Audit log"
    default_auto_field = "django.db.models.BigAutoField"
