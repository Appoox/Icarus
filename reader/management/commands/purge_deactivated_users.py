from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from reader.models import ReaderUser
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = f'Anonymizes accounts that have been deactivated for more than {settings.DAYS_BEFORE_PURGE} days.'

    def handle(self, *args, **options):
        retention_limit = timezone.now() - timedelta(days=settings.DAYS_BEFORE_PURGE)
        to_purge = ReaderUser.objects.filter(
            is_active=False,
            is_anonymized=False,
            deactivated_at__lte=retention_limit
        )
        
        count = to_purge.count()
        if count > 0:
            self.stdout.write(self.style.WARNING(f"Anonymizing (soft-purging) {count} deactivated accounts..."))
            done = failed = 0
            for user in to_purge.iterator():
                # One bad account must not stop the rest (and nothing is
                # half-done: anonymize_account() is a single transaction).
                try:
                    user.anonymize_account()
                    done += 1
                except Exception:
                    failed += 1
                    logger.exception("Could not anonymize reader id %s", user.pk)
            self.stdout.write(self.style.SUCCESS(f"Successfully anonymized {done} accounts."))
            if failed:
                self.stderr.write(self.style.ERROR(f"{failed} account(s) failed; see the log. They will be retried next run."))
        else:
            self.stdout.write(self.style.SUCCESS("No accounts ready for anonymization."))
