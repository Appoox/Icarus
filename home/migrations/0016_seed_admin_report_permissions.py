"""
Hand the new audit-log / analytics permissions to the groups that already had
access, so this change only ever loosens access, never removes it.

The old inline check in both views let in `is_staff`, superusers, and members
of the 'Editors' and 'Moderators' groups.  Staff and superusers still pass
unconditionally (see `home.permissions`), so only the two group names need
carrying over; 'Chief Editor' is seeded as well because that is the role the
permissions were asked for.

Only groups that already exist are touched — a group is never created here,
matching `literati.sync_editorial_staff`, which likewise expects role groups
to be made by hand.
"""
from django.db import migrations

PERMISSIONS = [
    ("view_auditlog", "Can view the audit log"),
    ("view_analytics", "Can view the analytics dashboard"),
]

SEED_GROUPS = ["Editors", "Moderators", "Chief Editor"]


def grant(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")

    # post_migrate creates these too, but it runs after every migration in the
    # run has finished — creating them here means the grant below can't race it.
    content_type, _ = ContentType.objects.get_or_create(
        app_label="home", model="adminreportaccess"
    )
    permissions = [
        Permission.objects.get_or_create(
            content_type=content_type, codename=codename, defaults={"name": name}
        )[0]
        for codename, name in PERMISSIONS
    ]

    for group in Group.objects.filter(name__in=SEED_GROUPS):
        group.permissions.add(*permissions)


def revoke(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Permission.objects.filter(
        content_type__app_label="home",
        codename__in=[codename for codename, _ in PERMISSIONS],
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("home", "0015_adminreportaccess"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]
