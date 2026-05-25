from django.apps import AppConfig


class KalapilaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'kalapila'

    def ready(self):
        from auditlog.registry import auditlog
        from .models import Comment, CommentReport, DashboardNotification

        # Track every field change on Comment (the core moderation model)
        auditlog.register(Comment)

        # Track who filed reports and when
        auditlog.register(CommentReport)

        # Track dashboard notification delivery / read status
        auditlog.register(DashboardNotification)