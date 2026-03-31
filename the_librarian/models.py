from django.db import models
from django.conf import settings
from pgvector.django import VectorField

# Create your models here.


class ArchiveDocument(models.Model):
    """Tracks which PDFs from the archive have been ingested."""
    filename = models.CharField(max_length=500, unique=True)
    file_path = models.CharField(
        max_length=1000,
        help_text="Relative path from ARCHIVE_DIR"
    )
    total_pages = models.IntegerField(default=0)
    ingested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-ingested_at']
        verbose_name = "Archive Document"
        verbose_name_plural = "Archive Documents"

    def __str__(self):
        return self.filename


class DocumentChunk(models.Model):
    """Stores semantically chunked + embedded text from archive PDFs."""
    document = models.ForeignKey(
        ArchiveDocument,
        on_delete=models.CASCADE,
        related_name='chunks'
    )
    page_number = models.IntegerField(
        help_text="Source page number in the PDF (1-indexed)"
    )
    chunk_text = models.TextField()
    embedding = VectorField(
        dimensions=settings.LIBRARIAN_EMBEDDING_DIM,
        help_text="pgvector embedding of the chunk text"
    )
    chunk_index = models.IntegerField(
        default=0,
        help_text="Order of this chunk within the document"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['document', 'chunk_index']
        verbose_name = "Document Chunk"
        verbose_name_plural = "Document Chunks"
        indexes = [
            models.Index(fields=['document', 'page_number']),
        ]

    def __str__(self):
        return f"{self.document.filename} — p.{self.page_number} chunk#{self.chunk_index}"
