"""
Management command: setup_disk_alert_schedule

Creates (or updates) the Django-Q2 Schedule that runs
home.tasks.check_disk_usage every hour, emailing ADMINS while the host disk is
nearly full. Idempotent: the web container runs it on every start (Dockerfile
CMD), like the other setup_*_schedule commands. Added after the 2026-10-22
disk-full incident.

Usage
-----
    # Create or update the hourly schedule
    python manage.py setup_disk_alert_schedule

    # Remove the schedule
    python manage.py setup_disk_alert_schedule --delete
"""
from django.core.management.base import BaseCommand
from django_q.models import Schedule

# Name used to look up and de-duplicate the schedule in the DB.
SCHEDULE_NAME = "Disk usage alert (auto)"

# The task the schedule runs.
TASK = "home.tasks.check_disk_usage"


class Command(BaseCommand):
    help = (
        f"Create or update the Django-Q2 schedule '{SCHEDULE_NAME}' that emails "
        "ADMINS every hour while the host disk is nearly full. Idempotent — safe "
        "to run multiple times."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--delete",
            action="store_true",
            help=f"Delete the schedule '{SCHEDULE_NAME}' instead of creating it.",
        )

    def handle(self, *args, delete=False, **options):
        if delete:
            deleted, _ = Schedule.objects.filter(name=SCHEDULE_NAME).delete()
            if deleted:
                self.stdout.write(self.style.SUCCESS(f"Deleted schedule '{SCHEDULE_NAME}'."))
            else:
                self.stdout.write(f"Schedule '{SCHEDULE_NAME}' not found — nothing to delete.")
            return

        # next_run is left out of defaults: a new schedule runs at the next
        # scheduler tick, and re-running this on every container start keeps
        # the existing hourly rhythm instead of resetting it.
        _, created = Schedule.objects.update_or_create(
            name=SCHEDULE_NAME,
            defaults={
                "func": TASK,
                # Hourly: a disk crossing the threshold is reported within the
                # hour, and every further hour above it sends a reminder.
                "schedule_type": Schedule.HOURLY,
                # -1 = repeat indefinitely; 0 would run once then stop
                "repeats": -1,
            },
        )

        verb = "Created" if created else "Updated"
        self.stdout.write(
            self.style.SUCCESS(f"{verb} schedule '{SCHEDULE_NAME}' ({TASK}, hourly).")
        )
