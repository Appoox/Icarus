from django.apps import AppConfig


class LiteratiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'literati'

    def ready(self):
        from auditlog.registry import auditlog
        from .models import (
            Literati, AuthorIndexPage, ArticleAuthorRelationship, 
            EditorialBoard, EditorialBoardMember
        )
        auditlog.register(Literati)
        auditlog.register(AuthorIndexPage)
        auditlog.register(ArticleAuthorRelationship)
        auditlog.register(EditorialBoard)
        auditlog.register(EditorialBoardMember)
