from django.apps import AppConfig


LITERATI_AUDIT_EXCLUDED_FIELDS = [
    'phone_number', 'email',
    'address_line_1', 'address_line_2', 'city', 'post_office', 'pincode',
    'district', 'state',
]


class LiteratiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'literati'

    def ready(self):
        from auditlog.registry import auditlog
        from .models import (
            Literati, AuthorIndexPage, ArticleAuthorRelationship, 
            EditorialBoard, EditorialBoardMember
        )
        # Author contact details stay out of the audit log (it is readable by
        # every holder of home.view_auditlog); the page itself keeps them.
        auditlog.register(Literati, exclude_fields=LITERATI_AUDIT_EXCLUDED_FIELDS)
        auditlog.register(AuthorIndexPage)
        auditlog.register(ArticleAuthorRelationship)
        auditlog.register(EditorialBoard)
        auditlog.register(EditorialBoardMember)
