"""
Keep audit-log / analytics access for staff who were appointed by hand.

home.permissions used to admit every is_staff user. It no longer does,
because sync_editorial_staff makes every current board member staff and the
audit log holds readers' personal data. So that no admin loses access in
the switch, staff that were NOT made staff by the board sync
(staff_granted_by_board=False) get the two permissions directly.

Board-granted staff get nothing here; grant the permissions to a role group
(Settings -> Groups) if a role should see these pages.
"""
from django.db import migrations

CODENAMES = ["view_auditlog", "view_analytics"]


def grant(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    ReaderUser = apps.get_model("reader", "ReaderUser")

    permissions = list(
        Permission.objects.filter(content_type__app_label="home", codename__in=CODENAMES)
    )
    if not permissions:
        return  # created by 0016; absent only if it was rolled back
    for user in ReaderUser.objects.filter(
        is_staff=True, is_superuser=False, staff_granted_by_board=False
    ):
        user.user_permissions.add(*permissions)


def revoke(apps, schema_editor):
    # Nothing to undo safely: we can't tell these grants from ones made by
    # hand afterwards, and the previous code admitted these users anyway.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("home", "0016_seed_admin_report_permissions"),
        ("reader", "0012_alter_paymentdetails_options_and_more"),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]
