"""
Management command: setup_backup_schedules

Creates (or updates) the Django-Q2 schedules of the off-server backup:

    Off-server backup (auto)      nightly, BACKUP_SCHEDULE_CRON (default 21:00 UTC = 02:30 IST)
    Backup verification (auto)    weekly, Saturday 22:30 UTC (Sunday 04:00 IST)
    Backup health alert (auto)    twice a day, 03:15 and 15:15 UTC (08:45 and 20:45 IST)

Idempotent: the web container runs it on every start (Dockerfile CMD), like
the other setup_*_schedule commands. A schedule keeps its next run time
unless its cron expression changed. Cron times are UTC (TIME_ZONE).

    python manage.py setup_backup_schedules
    python manage.py setup_backup_schedules --delete
"""
from datetime import datetime

from croniter import croniter
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django_q.models import Schedule

VERIFY_CRON = "30 22 * * 6"
ALERT_CRON = "15 3,15 * * *"


def schedules():
    """(name, task, cron) for each schedule this command manages."""
    return [
        ("Off-server backup (auto)", "backups.tasks.scheduled_backup", settings.BACKUP_SCHEDULE_CRON),
        ("Backup verification (auto)", "backups.tasks.scheduled_verify", VERIFY_CRON),
        ("Backup health alert (auto)", "backups.tasks.check_backup_health", ALERT_CRON),
    ]


class Command(BaseCommand):
    help = (
        "Create or update the Django-Q2 schedules for the nightly off-server backup, the weekly "
        "verification and the backup health alert. Idempotent."
    )

    def add_arguments(self, parser):
        parser.add_argument("--delete", action="store_true", help="Delete the schedules instead.")

    def handle(self, *args, delete=False, **options):
        if delete:
            names = [name for name, _, _ in schedules()]
            deleted, _ = Schedule.objects.filter(name__in=names).delete()
            self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} backup schedule(s)."))
            return

        for name, func, cron in schedules():
            if not croniter.is_valid(cron):
                raise CommandError(f"'{cron}' is not a valid cron expression (schedule '{name}').")
            existing = Schedule.objects.filter(name=name).first()
            defaults = {"func": func, "schedule_type": Schedule.CRON, "cron": cron, "repeats": -1}
            if existing is None or existing.cron != cron:
                # A new or re-timed schedule waits for its next slot instead of
                # firing on this container start.
                defaults["next_run"] = croniter(cron, timezone.now()).get_next(datetime)
            _, created = Schedule.objects.update_or_create(name=name, defaults=defaults)
            verb = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f"{verb} schedule '{name}' ({func}, cron '{cron}' UTC)."))
