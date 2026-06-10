from django.apps import AppConfig


class TheLibrarianConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'the_librarian'
    verbose_name = 'The Librarian'

    def ready(self):
        from auditlog.registry import auditlog
        # Import all three models that need audit logging
        from the_librarian.models import ArchiveDocument, DocumentChunk, ArchiveIssue

        # Guard against double-registration (e.g. during test runs with --reuse-db)
        if not auditlog.contains(ArchiveDocument):
            auditlog.register(ArchiveDocument)
        if not auditlog.contains(DocumentChunk):
            auditlog.register(DocumentChunk)
        if not auditlog.contains(ArchiveIssue):
            auditlog.register(ArchiveIssue)