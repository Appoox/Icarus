"""
Who may open the admin-only reporting pages (audit log, analytics).

Each of those views used to carry its own inline test —
``is_staff or is_superuser or groups__name__in=['Editors', 'Moderators']`` —
so the only way to let another role in was to edit Python and redeploy, and
the ``reader`` audit-log view carried no test at all.

Access now rides on two real Django permissions hung off
``home.models.AdminReportAccess``, which means it can be ticked per group in
Wagtail's Settings -> Groups screen (for a Chief Editor group, say) exactly
like every other permission.  Staff and superusers keep access
unconditionally, so nobody who could reach these pages before loses them.
"""

AUDIT_LOG_PERMISSION = "home.view_auditlog"
ANALYTICS_PERMISSION = "home.view_analytics"


def _has_report_access(user, permission):
    """Superusers and staff always; everyone else needs `permission`."""
    if user is None or not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    return user.has_perm(permission)


def can_view_audit_log(user):
    """True when `user` may read the audit log."""
    return _has_report_access(user, AUDIT_LOG_PERMISSION)


def can_view_analytics(user):
    """True when `user` may read the analytics dashboard."""
    return _has_report_access(user, ANALYTICS_PERMISSION)
