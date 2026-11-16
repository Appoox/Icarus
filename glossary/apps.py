from django.apps import AppConfig


class GlossaryConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'glossary'

    def ready(self):
        from auditlog.registry import auditlog
        from .models import GlossaryTerm
        auditlog.register(GlossaryTerm)
