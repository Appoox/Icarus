"""
Is the off-server backup healthy? One answer, used by the dashboard banner
and by the twice-daily alert email (backups.tasks.check_backup_health).

`problems` are worth an email: nothing is reaching Google Drive, or the
latest verification found a backup that would not restore. `warnings` show
on the dashboard only, e.g. one failed run while the last good backup is
still recent (the next scheduled run usually fixes it; if it doesn't, the
backup goes stale and becomes a problem).
"""
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from backups.models import BackupRun


def _latest(kind, **filters):
    return (
        BackupRun.objects.filter(kind=kind, **filters)
        .exclude(status__in=BackupRun.ACTIVE_STATUSES)
        .order_by("-created_at", "-pk")
        .first()
    )


def backup_health(now=None):
    now = now or timezone.now()
    max_age = timedelta(hours=settings.BACKUP_MAX_AGE_HOURS)
    last_ok = (
        BackupRun.objects.filter(kind=BackupRun.Kind.BACKUP, status=BackupRun.Status.SUCCEEDED)
        .order_by("-finished_at")
        .first()
    )
    last_backup = _latest(BackupRun.Kind.BACKUP)
    last_verify = _latest(BackupRun.Kind.VERIFY)

    problems, warnings = [], []
    if not settings.BACKUP_REMOTE:
        problems.append(
            "Off-server backups are not set up: BACKUP_REMOTE is empty in the server's .env."
        )
    if last_ok is None:
        problems.append("No backup has completed yet.")
    elif now - last_ok.finished_at > max_age:
        hours = int((now - last_ok.finished_at).total_seconds() // 3600)
        problems.append(
            f"The last complete backup finished {hours} hours ago, more than the "
            f"{settings.BACKUP_MAX_AGE_HOURS}-hour limit."
        )
    if last_backup is not None and last_backup.status == BackupRun.Status.FAILED:
        message = f"The latest backup (#{last_backup.pk}) failed: {last_backup.error}"
        if problems:
            # Already worth an email; this is likely why, so say it there too.
            problems.append(message)
        else:
            warnings.append(message)
    if last_verify is not None and last_verify.status == BackupRun.Status.FAILED:
        problems.append(
            f"The latest verification (#{last_verify.pk}) failed, so the backups may not "
            f"restore: {last_verify.error}"
        )

    return {
        "ok": not problems and not warnings,
        "problems": problems,
        "warnings": warnings,
        "last_ok": last_ok,
        "last_backup": last_backup,
        "last_verify": last_verify,
    }
