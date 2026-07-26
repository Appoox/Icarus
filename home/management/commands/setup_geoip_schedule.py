"""
Management command: setup_geoip_schedule

Creates (or updates) a Django-Q2 Schedule that refreshes the offline GeoIP
database (home.tasks.refresh_geoip_db) once a month. Run this once after
deployment; it is idempotent. Mirrors articles/.../setup_page_lock_schedule.py.

Usage
-----
    # Create the default monthly schedule (03:00 UTC on the 3rd)
    python manage.py setup_geoip_schedule

    # Custom cron
    python manage.py setup_geoip_schedule --cron "0 4 5 * *"

    # Remove the schedule
    python manage.py setup_geoip_schedule --delete
"""
from django.core.management.base import BaseCommand

# Name used to look up and de-duplicate the schedule in the DB.
SCHEDULE_NAME = "Refresh GeoIP database (auto)"


class Command(BaseCommand):
    help = (
        f"Create or update the Django-Q2 schedule '{SCHEDULE_NAME}' that "
        "refreshes the offline GeoIP database monthly. Idempotent — safe to run "
        "multiple times."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--cron",
            type=str,
            default="0 3 3 * *",
            help=(
                "Cron expression for the refresh. Default: '0 3 3 * *' — 03:00 "
                "UTC on the 3rd of each month, a couple of days after DB-IP "
                "publishes the new monthly build."
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
                self.stdout.write(
                    f"Schedule '{SCHEDULE_NAME}' not found — nothing to delete."
                )
            return

        cron = options["cron"]

        schedule, created = Schedule.objects.update_or_create(
            name=SCHEDULE_NAME,
            defaults={
                "func": "home.tasks.refresh_geoip_db",
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
                f"  Function : home.tasks.refresh_geoip_db\n"
                f"  Cron     : {cron}\n"
                f"  Repeats  : indefinitely\n\n"
                "Make sure qcluster is running to execute the schedule:\n"
                "  python manage.py qcluster"
            )
        )
