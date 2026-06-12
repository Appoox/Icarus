from django.db import models
from django.conf import settings
from django.db.models import GeneratedField
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.contrib.postgres.indexes import GinIndex
from pgvector.django import VectorField
from wagtail.admin.panels import FieldPanel, MultiFieldPanel

import logging

logger = logging.getLogger(__name__)


# ── Publication metadata constants ───────────────────────────────────────

MONTH_CHOICES = [
    (1, 'January'), (2, 'February'), (3, 'March'),
    (4, 'April'), (5, 'May'), (6, 'June'),
    (7, 'July'), (8, 'August'), (9, 'September'),
    (10, 'October'), (11, 'November'), (12, 'December'),
]

# Malayalam month names for front-end display
MALAYALAM_MONTHS = {
    1: 'ജനുവരി', 2: 'ഫെബ്രുവരി', 3: 'മാർച്ച്',
    4: 'ഏപ്രിൽ', 5: 'മേയ്', 6: 'ജൂൺ',
    7: 'ജൂലൈ', 8: 'ഓഗസ്റ്റ്', 9: 'സെപ്തംബർ',
    10: 'ഒക്ടോബർ', 11: 'നവംബർ', 12: 'ഡിസംബർ',
}


# ── Archive models ────────────────────────────────────────────────────────

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
        related_name='chunks',
        null=True,
        blank=True
    )
    article = models.ForeignKey(
        'articles.Article',
        on_delete=models.CASCADE,
        related_name='chunks',
        null=True,
        blank=True
    )
    author = models.ForeignKey(
        'literati.Literati',
        on_delete=models.CASCADE,
        related_name='chunks',
        null=True,
        blank=True
    )
    page_number = models.IntegerField(
        null=True,
        blank=True,
        help_text="Source page number in the PDF (1-indexed)"
    )
    chunk_text = models.TextField()

    # Pre-computed Search Vector for fast Full-Text Search
    search_vector = GeneratedField(
        expression=SearchVector("chunk_text", config="simple"),
        output_field=SearchVectorField(),
        db_persist=True,
    )

    embedding = VectorField(
        dimensions=settings.LIBRARIAN_EMBEDDING_DIM,
        help_text="pgvector embedding of the chunk text"
    )
    language = models.CharField(
        max_length=10,
        default='ml',
        help_text="ISO language code of the chunk text"
    )
    chunk_index = models.IntegerField(
        default=0,
        help_text="Order of this chunk within the document/article/author"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['document', 'chunk_index']
        verbose_name = "Document Chunk"
        verbose_name_plural = "Document Chunks"
        indexes = [
            models.Index(fields=['document', 'page_number']),
            GinIndex(fields=["search_vector"]),
        ]

    def __str__(self):
        if self.document_id:
            return f"{self.document.filename} — p.{self.page_number} chunk#{self.chunk_index}"
        elif self.article_id:
            return f"Article: {self.article.title} — chunk#{self.chunk_index}"
        elif self.author_id:
            return f"Author: {self.author.title} — chunk#{self.chunk_index}"
        return f"Unknown Source — chunk#{self.chunk_index}"


# ── Archive Issue (admin-managed uploads) ───────────────────────────────

class ArchiveIssue(models.Model):
    """
    Admins upload the PDF via the Wagtail snippet admin; metadata (year, month,
    volume, issue number) replaces the raw filename on the public archive page.

    PDFs are stored in MEDIA_ROOT/archive/.  To also make them available to the
    ingestion pipeline, set ARCHIVE_DIR = os.path.join(MEDIA_ROOT, 'archive')
    in your Django settings.

    Thumbnails are auto-generated from the PDF's first page. Admins can also upload a thumbnail manually.
    """

    title = models.CharField(
        max_length=500,
        help_text="Display title, e.g. 'ജനുവരി 2026'"
    )
    year = models.IntegerField(db_index=True)
    month = models.IntegerField(choices=MONTH_CHOICES, db_index=True)
    volume = models.IntegerField(db_index=True, help_text="Volume number")
    issue_number = models.IntegerField(
        verbose_name="Issue No.",
        help_text="Issue number within the volume"
    )
    description = models.TextField(
        blank=True,
        help_text="Optional editorial note or summary shown on the archive page"
    )

    # ── Files ──
    pdf_file = models.FileField(
        upload_to='archive/',
        help_text="Upload the PDF.  Stored at MEDIA_ROOT/archive/."
    )
    thumbnail = models.ImageField(
        upload_to='archive/thumbnails/',
        blank=True,
        null=True,
        help_text="Auto-generated if nothing is uploaded"
    )

    # ── Optional search integration ──
    archive_document = models.OneToOneField(
        'ArchiveDocument',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='Archive_Issue',
        help_text=(
            "Link to the ingested ArchiveDocument for full-text search.  "
            "Set automatically if you ingest the PDF after uploading."
        )
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-year', '-month']
        verbose_name = "Archive Issue"
        verbose_name_plural = "Archive Issues"

    def __str__(self):
        return f"{self.title} — Vol.{self.volume} No.{self.issue_number} ({self.year})"

    # ── Computed properties ──

    @property
    def month_name(self):
        """English month name."""
        return dict(MONTH_CHOICES).get(self.month, str(self.month))

    @property
    def month_name_ml(self):
        """Malayalam month name."""
        return MALAYALAM_MONTHS.get(self.month, '')

    # ── Save / thumbnail generation ──

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        old_pdf_name = None
        if not is_new:
            try:
                old_pdf_name = ArchiveIssue.objects.get(pk=self.pk).pdf_file.name
            except ArchiveIssue.DoesNotExist:
                pass

        super().save(*args, **kwargs)

        # Auto-generate thumbnail when PDF is first uploaded or replaced
        if self.pdf_file and not self.thumbnail:
            pdf_changed = is_new or (
                old_pdf_name is not None and old_pdf_name != self.pdf_file.name
            )
            if pdf_changed:
                self._auto_generate_thumbnail()

    def _auto_generate_thumbnail(self):
        """
        Renders the PDF first page as a PNG and saves it as the thumbnail.
        Requires PyMuPDF:  pip install pymupdf
        """
        try:
            import fitz  # PyMuPDF
            from django.core.files.base import ContentFile

            with fitz.open(self.pdf_file.path) as doc:
                page = doc[0]
                # 2× scale gives ~150 dpi for an A4 page → good cover quality
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                img_bytes = pix.tobytes("png")

            thumb_name = (
                f"thumb_{self.year}_{self.month:02d}"
                f"_v{self.volume}_i{self.issue_number}.png"
            )
            self.thumbnail.save(thumb_name, ContentFile(img_bytes), save=False)
            # Update only the thumbnail field to avoid triggering save() again
            type(self).objects.filter(pk=self.pk).update(thumbnail=self.thumbnail.name)
            logger.info("Auto-generated thumbnail for ArchiveIssue pk=%s", self.pk)

        except ImportError:
            logger.warning(
                "PyMuPDF not installed — skipping auto-thumbnail for pk=%s. "
                "Run:  pip install pymupdf", self.pk
            )
        except Exception as exc:
            logger.warning(
                "Thumbnail generation failed for ArchiveIssue pk=%s: %s", self.pk, exc
            )

    # ── Wagtail admin panels ──────────────────────────────────────────────

    panels = [
        MultiFieldPanel([
            FieldPanel('title'),
            FieldPanel('description'),
        ], heading="Issue Information"),
        MultiFieldPanel([
            FieldPanel('year'),
            FieldPanel('month'),
            FieldPanel('volume'),
            FieldPanel('issue_number'),
        ], heading="Publication Details"),
        MultiFieldPanel([
            FieldPanel('pdf_file'),
            FieldPanel('thumbnail'),
        ], heading="Files"),
        FieldPanel('archive_document'),
    ]