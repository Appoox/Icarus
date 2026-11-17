"""
Django-Q2 entry points for the backups app, run by the qcluster worker.

The schedules (``manage.py setup_backup_schedules``) call the scheduled_*
functions; the dashboard queues perform_run with the row it created, so the
admin who clicked can watch that row.
"""
import time

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import mail_admins
from django.urls import reverse

from backups import engine
from backups.health import backup_health
from backups.models import BackupRun


def _deadline():
    """When this task's media upload must stop, ahead of the Q_CLUSTER timeout."""
    timeout = settings.Q_CLUSTER.get("timeout")
    if not timeout:
        return None
    return time.monotonic() + timeout - engine.DEADLINE_MARGIN.total_seconds()


def perform_run(run_id, snapshot=None):
    """Carry out a run the dashboard queued."""
    run = BackupRun.objects.get(pk=run_id)
    if run.status != BackupRun.Status.QUEUED:
        # Django-Q redelivered a task whose first attempt already ran.
        return run.status
    engine.perform(run, deadline=_deadline(), snapshot=snapshot)
    return run.status


def scheduled_backup():
    run = BackupRun.objects.create(kind=BackupRun.Kind.BACKUP, trigger=BackupRun.Trigger.SCHEDULED)
    engine.perform(run, deadline=_deadline())
    return run.snapshot


def scheduled_verify():
    run = BackupRun.objects.create(kind=BackupRun.Kind.VERIFY, trigger=BackupRun.Trigger.SCHEDULED)
    engine.perform(run, deadline=_deadline())
    return run.snapshot


def check_backup_health():
    """
    Email ADMINS while the off-server backup needs attention.

    Like home.tasks.check_disk_usage, it fails loudly rather than sending
    nothing: with ADMINS empty it raises, so the gap shows up as a failed
    task in the Django-Q admin. Returns True when all is well.
    """
    if not settings.ADMINS:
        raise ImproperlyConfigured(
            "ADMINS is empty, so the backup alert has nobody to email. "
            "Set ADMIN_EMAILS in the server's .env."
        )
    health = backup_health()
    if health["problems"]:
        dashboard = settings.WAGTAILADMIN_BASE_URL + reverse("backups_dashboard")
        lines = [f"- {problem}" for problem in health["problems"]]
        # Admin-only and English, like the disk alert: no reader sees it.
        mail_admins(
            subject="Backups need attention on icarus-flight",
            message=(
                "The off-server backup of sasthragathy.org needs attention:\n\n"
                + "\n".join(lines)
                + f"\n\nDashboard (logs, setup checks, 'Back up now'): {dashboard}\n"
                "Setup and troubleshooting: backups/README.md in the repository.\n"
            ),
        )
    return not health["problems"]
