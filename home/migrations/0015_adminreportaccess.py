"""
Register the permission-carrier model for the admin reporting pages.

`AdminReportAccess` is unmanaged, so this creates no table — it only puts the
model in the migration state, which is what gets its content type and the two
permissions (`view_auditlog`, `view_analytics`) created on migrate.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("home", "0014_seed_homepage_layout"),
    ]

    operations = [
        migrations.CreateModel(
            name="AdminReportAccess",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
            ],
            options={
                "verbose_name": "admin reports",
                "verbose_name_plural": "admin reports",
                "permissions": [
                    ("view_auditlog", "Can view the audit log"),
                    ("view_analytics", "Can view the analytics dashboard"),
                ],
                "managed": False,
                "default_permissions": (),
            },
        ),
    ]
