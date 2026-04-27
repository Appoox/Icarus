from django.apps import AppConfig


class IssueConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'issue'

    def ready(self):
        from auditlog.registry import auditlog
        from .models import Volume, Issue, Topic, IssueIndexPage
        auditlog.register(Volume)
        auditlog.register(Issue)
        auditlog.register(Topic)
        auditlog.register(IssueIndexPage)
