from django.contrib import admin

# ArchiveIssue is intentionally NOT registered here.
# All upload/edit/delete of Archive Issues must go through the Wagtail admin:
#   /admin/snippets/the_librarian/ArchiveIssue/
# Registering it in Django admin would create a second upload path that bypasses
# Wagtail's permission model and audit trail.
from the_librarian.models import ArchiveDocument, DocumentChunk


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ArchiveDocument — read-only; records are created by the ingestion pipeline
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@admin.register(ArchiveDocument)
class ArchiveDocumentAdmin(admin.ModelAdmin):
    list_display    = ("filename", "total_pages", "ingested_at")
    list_filter     = ("ingested_at",)
    search_fields   = ("filename",)
    readonly_fields = ("filename", "file_path", "total_pages", "ingested_at")

    def has_add_permission(self, request):
        # Records are created by the ingestion pipeline, not manually
        return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DocumentChunk — read-only; records are created by the ingestion pipeline
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@admin.register(DocumentChunk)
class DocumentChunkAdmin(admin.ModelAdmin):
    list_display    = ("document", "page_number", "chunk_index", "short_text", "created_at")
    list_filter     = ("document", "page_number")
    search_fields   = ("chunk_text",)
    readonly_fields = (
        "document", "page_number", "chunk_text",
        "chunk_index", "embedding", "created_at",
    )

    def short_text(self, obj):
        """Truncated preview of the chunk text."""
        return obj.chunk_text[:100] + "…" if len(obj.chunk_text) > 100 else obj.chunk_text
    short_text.short_description = "Chunk Text"

    def has_add_permission(self, request):
        # Records are created by the ingestion pipeline, not manually
        return False