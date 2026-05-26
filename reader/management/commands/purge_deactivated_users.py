from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from reader.models import ReaderUser
from django.conf import settings

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
            for user in to_purge:
                user.anonymize_account()
            self.stdout.write(self.style.SUCCESS(f"Successfully anonymized {count} accounts."))
        else:
            self.stdout.write(self.style.SUCCESS("No accounts ready for anonymization."))
