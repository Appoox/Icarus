"""
Who may open the admin-only reporting pages (audit log, analytics).

Each of those views used to carry its own inline test —
``is_staff or is_superuser or groups__name__in=['Editors', 'Moderators']`` —
so the only way to let another role in was to edit Python and redeploy, and
the ``reader`` audit-log view carried no test at all.

Access now rides on two real Django permissions hung off
``home.models.AdminReportAccess``, which means it can be ticked per group in
Wagtail's Settings -> Groups screen (for a Chief Editor group, say) exactly
like every other permission.  Only superusers pass unconditionally.

`is_staff` is deliberately NOT enough: literati.sync_editorial_staff grants
is_staff to every current board member, and the audit log holds readers'
personal data. Staff who were appointed by hand received the permissions
directly (migration home 0017); board members get them only through a
group an admin chooses to grant them to.
"""

AUDIT_LOG_PERMISSION = "home.view_auditlog"
ANALYTICS_PERMISSION = "home.view_analytics"


def _has_report_access(user, permission):
    """Superusers always; everyone else needs `permission` (directly or via a group)."""
    if user is None or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.has_perm(permission)


def can_view_audit_log(user):
    """True when `user` may read the audit log."""
    return _has_report_access(user, AUDIT_LOG_PERMISSION)


def can_view_analytics(user):
    """True when `user` may read the analytics dashboard."""
    return _has_report_access(user, ANALYTICS_PERMISSION)
