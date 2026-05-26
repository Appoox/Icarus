from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import models
from datetime import timedelta
from reader.models import ReaderUser, SubscriptionHistory, PaymentDetails

class Command(BaseCommand):
    help = 'Permanently deletes anonymized records where the subscription end date (or deactivation date) is older than 8 years.'

    def handle(self, *args, **options):
        # 8 years retention limit
        retention_limit = timezone.now() - timedelta(days=8 * 365)
        
        # Find anonymized users whose subscription_end is older than 8 years,
        # or who never had a subscription and deactivated_at is older than 8 years.
        to_delete = ReaderUser.objects.filter(
            is_anonymized=True
        ).filter(
            models.Q(subscription_end__lte=retention_limit) | 
            models.Q(subscription_end__isnull=True, deactivated_at__lte=retention_limit)
        )
        
        count = to_delete.count()
        if count > 0:
            self.stdout.write(self.style.WARNING(f"Permanently deleting {count} expired anonymized accounts..."))
            
            for user in to_delete:
                # Find and delete associated histories
                histories = SubscriptionHistory.objects.filter(reader=user)
                
                # Find and delete associated payments
                payment_ids = histories.values_list('payment_details_id', flat=True)
                PaymentDetails.objects.filter(id__in=payment_ids).delete()
                
                # Delete user-level payments if any
                if user.payment_details:
                    user.payment_details.delete()
                
                # Explicitly delete histories and then the user row
                histories.delete()
                user.delete()
                
            self.stdout.write(self.style.SUCCESS(f"Successfully deleted {count} expired anonymized records."))
        else:
            self.stdout.write(self.style.SUCCESS("No expired anonymized records found."))
