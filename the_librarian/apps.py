from django.apps import AppConfig


class TheLibrarianConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'the_librarian'
    verbose_name = 'The Librarian'

    def ready(self):
        from auditlog.registry import auditlog
        from .models import ArchiveDocument, DocumentChunk
        auditlog.register(ArchiveDocument)
        auditlog.register(DocumentChunk)
