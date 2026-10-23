from django.apps import AppConfig


class TheLibrarianConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'the_librarian'
    verbose_name = 'The Librarian'

    def ready(self):
        from . import checks  # noqa: F401  (registers the embedding-size check)
        from auditlog.registry import auditlog
        from the_librarian.models import ArchiveDocument, ArchiveIssue

        # DocumentChunk is deliberately NOT audited: chunks are derived data,
        # rebuilt by every ingestion, and each audited save stored the full
        # chunk text and a 1024-number embedding diff in the audit log.
        # Guard against double-registration (e.g. during test runs with --reuse-db)
        if not auditlog.contains(ArchiveDocument):
            auditlog.register(ArchiveDocument)
        if not auditlog.contains(ArchiveIssue):
            auditlog.register(ArchiveIssue)