from django.apps import AppConfig
from wagtail.users.apps import WagtailUsersAppConfig

class ReaderConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'reader'

    def ready(self):
        from auditlog.registry import auditlog
        from .models import ReaderUser, PaymentDetails
        auditlog.register(ReaderUser)
        auditlog.register(PaymentDetails)

class CustomWagtailUsersAppConfig(WagtailUsersAppConfig):
    user_viewset = "reader.viewsets.ReaderUserViewSet"