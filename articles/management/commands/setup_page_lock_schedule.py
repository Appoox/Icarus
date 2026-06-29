"""
Management command: setup_page_lock_schedule

Creates (or updates) a Django-Q2 Schedule that runs lock_old_pages_task()
on a cron schedule.  Run this once after deployment; it is idempotent.

Usage
-----
    # Create the default nightly-at-02:00 schedule
    python manage.py setup_page_lock_schedule

    # Custom threshold and run time
    python manage.py setup_page_lock_schedule --days 30 --cron "0 3 * * *"

    # Remove the schedule
    python manage.py setup_page_lock_schedule --delete
"""
import json
from django.core.management.base import BaseCommand
from django.conf import settings

# Name used to look up and de-duplicate the schedule in the DB.
SCHEDULE_NAME = "Lock old pages (auto)"


class Command(BaseCommand):
    help = (
        f"Create or update the Django-Q2 schedule '{SCHEDULE_NAME}' that runs "
        "lock_old_pages_task() nightly.  Idempotent — safe to run multiple times."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help=(
                "Threshold passed to lock_old_pages_task. "
                "Defaults to PAGE_LOCK_DAYS in settings "
                f"(currently {getattr(settings, 'PAGE_LOCK_DAYS', 10)})."
            ),
        )
        parser.add_argument(
            "--cron",
            type=str,
            default="0 2 * * *",
            help=(
                "Cron expression for the schedule. "
                "Default: '0 2 * * *' (02:00 UTC daily). "
                "Icarus runs on IST (UTC+5:30), so 02:00 UTC = 07:30 IST."
            ),
        )
        parser.add_argument(
            "--delete",
            action="store_true",
            help=f"Delete the schedule '{SCHEDULE_NAME}' instead of creating it.",
        )

    def handle(self, *args, **options):
        try:
            from django_q.models import Schedule
        except ImportError:
            self.stderr.write(
                self.style.ERROR(
                    "django_q is not installed. "
                    "Add 'django-q2' to requirements.txt and re-run."
                )
            )
            return

        if options["delete"]:
            deleted, _ = Schedule.objects.filter(name=SCHEDULE_NAME).delete()
            if deleted:
                self.stdout.write(
                    self.style.SUCCESS(f"Deleted schedule '{SCHEDULE_NAME}'.")
                )
            else:
                self.stdout.write(f"Schedule '{SCHEDULE_NAME}' not found — nothing to delete.")
            return

        days = options["days"] or getattr(settings, "PAGE_LOCK_DAYS", 10)
        cron = options["cron"]

        schedule, created = Schedule.objects.update_or_create(
            name=SCHEDULE_NAME,
            defaults={
                "func": "articles.tasks.lock_old_pages_task",
                # kwargs is stored as a JSON string by Django-Q2
                "kwargs": json.dumps({"days": days}),
                "schedule_type": Schedule.CRON,
                "cron": cron,
                # -1 = repeat indefinitely; 0 would run once then stop
                "repeats": -1,
            },
        )

        verb = "Created" if created else "Updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} schedule '{SCHEDULE_NAME}'.\n\n"
                f"  Function  : articles.tasks.lock_old_pages_task\n"
                f"  Threshold : {days} day(s)\n"
                f"  Cron      : {cron}\n"
                f"  Repeats   : indefinitely\n\n"
                "Make sure qcluster is running to execute the schedule:\n"
                "  python manage.py qcluster\n\n"
                "To preview what would be locked right now:\n"
                f"  python manage.py lock_old_pages --days {days} --dry-run"
            )
        )
