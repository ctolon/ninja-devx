import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

from ninja_devx.contrib.grants.models import USER_XOR_GROUP, check_constraint


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ObjectGrant",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("object_pk", models.CharField(max_length=255)),
                ("created", models.DateTimeField(auto_now_add=True)),
                (
                    "content_type",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="contenttypes.contenttype"
                    ),
                ),
                (
                    "group",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        to="auth.group",
                    ),
                ),
                (
                    "permission",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="auth.permission"
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["content_type", "object_pk"], name="ninja_devx__content_424f12_idx"
                    )
                ],
                "constraints": [
                    # Written with the helper so the migration works on Django 4.2 to 6.x.
                    check_constraint(USER_XOR_GROUP, "ninja_devx_grant_user_xor_group"),
                    models.UniqueConstraint(
                        condition=models.Q(("user__isnull", False)),
                        fields=("permission", "content_type", "object_pk", "user"),
                        name="ninja_devx_grant_unique_user",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(("group__isnull", False)),
                        fields=("permission", "content_type", "object_pk", "group"),
                        name="ninja_devx_grant_unique_group",
                    ),
                ],
            },
        ),
    ]
