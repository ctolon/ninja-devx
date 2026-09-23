from django.apps import AppConfig


class JobsConfig(AppConfig):
    name = "ninja_devx.contrib.jobs"
    label = "ninja_devx_jobs"
    verbose_name = "Jobs"
    default_auto_field = "django.db.models.BigAutoField"
