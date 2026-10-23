from django.apps import AppConfig
from wagtail.users.apps import WagtailUsersAppConfig


class ReaderConfig(AppConfig):
    # This module defines (and imports) more than one AppConfig subclass, so
    # Django can't pick one for a bare "reader" entry and silently used a
    # plain AppConfig: ready() never ran, the newsletter receiver was never
    # connected and PaymentDetails was never audited. default=True (and the
    # explicit dotted path in INSTALLED_APPS) make this the reader config.
    default = True
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'reader'

    def ready(self):
        from auditlog.registry import auditlog
        from .models import PaymentDetails

        # ReaderUser is registered in models.py, with its personal-data
        # exclusions; registering it again here would replace that.
        auditlog.register(PaymentDetails)

        # Connect the Issue-published → newsletter receiver.
        from . import signals  # noqa: F401


class CustomWagtailUsersAppConfig(WagtailUsersAppConfig):
    user_viewset = "reader.viewsets.ReaderUserViewSet"
