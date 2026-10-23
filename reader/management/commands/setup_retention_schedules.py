"""
Schedule the data-retention purges so they actually run.

    python manage.py setup_retention_schedules          # create / update
    python manage.py setup_retention_schedules --delete # remove

Idempotent (run on every container start, like setup_page_lock_schedule).
Requires the qcluster service to be running.

  * purge_deactivated_users — daily: anonymises accounts deactivated more
    than DAYS_BEFORE_PURGE days ago (the privacy policy's promise).
  * purge_expired_data — weekly: deletes anonymised records past the
    8-year retention period.
"""
from django.core.management.base import BaseCommand

SCHEDULES = [
    # (name, management command, cron — UTC)
    ("Anonymise deactivated readers (auto)", "purge_deactivated_users", "15 3 * * *"),
    ("Delete records past 8-year retention (auto)", "purge_expired_data", "45 3 * * 0"),
]


class Command(BaseCommand):
    help = "Create or update the Django-Q2 schedules for the data-retention purges."

    def add_arguments(self, parser):
        parser.add_argument("--delete", action="store_true", help="Remove the schedules instead.")

    def handle(self, *args, delete=False, **options):
        from django_q.models import Schedule

        for name, command, cron in SCHEDULES:
            if delete:
                Schedule.objects.filter(name=name).delete()
                self.stdout.write(f"Removed schedule '{name}'.")
                continue
            _, created = Schedule.objects.update_or_create(
                name=name,
                defaults={
                    "func": "django.core.management.call_command",
                    "args": repr(command),   # django-q literal_evals this
                    "schedule_type": Schedule.CRON,
                    "cron": cron,
                    "repeats": -1,
                },
            )
            self.stdout.write(f"{'Created' if created else 'Updated'} schedule '{name}' ({cron} UTC).")
