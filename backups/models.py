"""
One row per backup, verification or setup check, so the admin dashboard and
the health alert can see what happened without asking Google Drive.

These rows describe the backups; they are not the backups. Those live on the
rclone remote (BACKUP_REMOTE), and restore.sh finds them there by itself:
after a disaster this table is gone along with the rest of the database.
"""
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.db import models

# Snapshot IDs are UTC timestamps, so they sort in time order as plain text.
SNAPSHOT_FORMAT = "%Y%m%dT%H%M%SZ"


def snapshot_time(snapshot_id):
    """The aware UTC datetime a snapshot ID names, or None if it isn't one."""
    try:
        return datetime.strptime(snapshot_id, SNAPSHOT_FORMAT).replace(tzinfo=dt_timezone.utc)
    except (TypeError, ValueError):
        return None


class BackupRun(models.Model):
    """A backup, verification or setup check: queued, running or finished."""

    class Kind(models.TextChoices):
        BACKUP = "backup", "Backup"
        VERIFY = "verify", "Verification"
        CHECK = "check", "Setup check"

    class Trigger(models.TextChoices):
        SCHEDULED = "scheduled", "Schedule"
        MANUAL = "manual", "Dashboard"
        COMMAND = "command", "Command line"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    ACTIVE_STATUSES = (Status.QUEUED, Status.RUNNING)

    kind = models.CharField(max_length=16, choices=Kind.choices)
    trigger = models.CharField(max_length=16, choices=Trigger.choices)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.QUEUED, db_index=True
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    snapshot = models.CharField(
        max_length=32,
        blank=True,
        help_text="The snapshot this run made or checked: a UTC timestamp, e.g. 20261116T210000Z.",
    )
    db_bytes = models.BigIntegerField(null=True, blank=True)
    # The off-server media copy after this run, and what this run changed in it.
    media_files = models.IntegerField(null=True, blank=True)
    media_bytes = models.BigIntegerField(null=True, blank=True)
    media_uploaded = models.IntegerField(null=True, blank=True)
    media_archived = models.IntegerField(null=True, blank=True)

    # Setup checks, the snapshot list, remote storage usage, verify findings.
    details = models.JSONField(default=dict, blank=True)
    log = models.TextField(blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        # Only the one permission below: nobody adds or edits these by hand.
        default_permissions = ()
        permissions = [("manage_backups", "Can run and monitor off-server backups")]
        verbose_name = "backup run"

    def __str__(self):
        return f"{self.get_kind_display()} #{self.pk} ({self.get_status_display()})"

    @property
    def is_active(self):
        return self.status in self.ACTIVE_STATUSES

    @property
    def duration(self):
        if self.started_at and self.finished_at:
            return self.finished_at - self.started_at
        return None

    @property
    def duration_display(self):
        """'45s', '3m 12s', '1h 05m'; '' while unfinished."""
        if self.duration is None:
            return ""
        minutes, seconds = divmod(int(self.duration.total_seconds()), 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}h {minutes:02d}m"
        if minutes:
            return f"{minutes}m {seconds:02d}s"
        return f"{seconds}s"

    @property
    def snapshot_time(self):
        return snapshot_time(self.snapshot)
