"""
Who may open the backups dashboard and start backups.

Superusers always; anyone else needs ``backups.manage_backups``, granted per
group in Wagtail's Settings -> Groups (the same shape as home.permissions).
Nobody else, staff included: the dashboard shows how the server's backups are
reached, and the snapshots it lists hold every reader's personal data.
"""

MANAGE_BACKUPS_PERMISSION = "backups.manage_backups"


def can_manage_backups(user):
    """True when `user` may see the backups dashboard and start runs."""
    if user is None or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.has_perm(MANAGE_BACKUPS_PERMISSION)
