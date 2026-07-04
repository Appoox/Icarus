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
        help_text="Relative path from ARCHIVE_DIR (or MEDIA_ROOT for ArchiveIssue uploads)"
    )
    total_pages = models.IntegerField(default=0)
    ingested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-ingested_at']
        verbose_name = "Archive Document"
        verbose_name_plural = "Archive Documents"

    def __str__(self):
        return self.display_title

    @property
    def display_title(self):
        """
        Returns the human-readable title of the associated ArchiveIssue if it exists,
        otherwise falls back to the raw filename.
        """
        if hasattr(self, 'Archive_Issue') and self.Archive_Issue:
            return self.Archive_Issue.title
        return self.filename


class DocumentChunk(models.Model):
    """Stores semantically chunked + embedded text from archive PDFs, articles, authors, issues, and topics."""
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
    # ── Issue editorial chunks ─────────────────────────────────────────────
    issue = models.ForeignKey(
        'issue.Issue',
        on_delete=models.CASCADE,
        related_name='chunks',
        null=True,
        blank=True
    )
    # ── NEW: Related Topic chunks ──────────────────────────────────────────
    topic = models.ForeignKey(
        'issue.Topic',
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
        help_text="Order of this chunk within the document/article/author/issue"
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
            return f"{self.document.display_title} — p.{self.page_number} chunk#{self.chunk_index}"
        elif self.article_id:
            return f"Article: {self.article.title} — chunk#{self.chunk_index}"
        elif self.author_id:
            return f"Author: {self.author.title} — chunk#{self.chunk_index}"
        elif self.issue_id:
            return f"Editorial: {self.issue.title} — chunk#{self.chunk_index}"
        elif self.topic_id:
            return f"Topic: {self.topic.name} — chunk#{self.chunk_index}"
        return f"Unknown Source — chunk#{self.chunk_index}"


# ── Archive Issue (admin-managed uploads) ───────────────────────────────

class ArchiveIssue(models.Model):
    """
    Admins upload the PDF via the Wagtail snippet admin; metadata (year, month,
    volume, issue number) replaces the raw filename on the public archive page.

    PDFs are stored in MEDIA_ROOT/archive/.  To also make them available to the
    ingestion pipeline, set ARCHIVE_DIR = os.path.join(MEDIA_ROOT, 'archive')
    in your Django settings.

    On save, if the PDF is new or replaced, an async Django-Q2 task is queued
    to OCR/chunk/embed the PDF into DocumentChunk records.
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
            "Set automatically when ingestion completes after uploading."
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

    # ── Save / thumbnail generation / async ingestion queuing ──

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        old_pdf_name = None
        if not is_new:
            try:
                old_pdf_name = ArchiveIssue.objects.get(pk=self.pk).pdf_file.name
            except ArchiveIssue.DoesNotExist:
                pass

        super().save(*args, **kwargs)

        # Determine whether the PDF is new or has been replaced
        pdf_changed = is_new or (
            old_pdf_name is not None
            and self.pdf_file
            and old_pdf_name != self.pdf_file.name
        )

        # Auto-generate thumbnail when PDF is first uploaded or replaced
        if self.pdf_file and not self.thumbnail and pdf_changed:
            self._auto_generate_thumbnail()

        # Queue async OCR/ingestion task when PDF is new or replaced
        if self.pdf_file and pdf_changed:
            self._queue_ingestion_task()
            self._queue_page_prerender_task()

    def _queue_ingestion_task(self):
        """
        Queue a Django-Q2 async task to ingest this issue's PDF.
        Uses transaction.on_commit so the task is queued only after the DB
        transaction commits — preventing the worker from starting before the
        new PDF record is visible to other connections.
        Silently skips if django_q is not installed.
        """
        try:
            from django.db import transaction
            from django_q.tasks import async_task
            _pk = self.pk
            transaction.on_commit(lambda: async_task(
                'the_librarian.tasks.async_ingest_archive_issue', _pk
            ))
            logger.info(
                "Queued async ingestion for ArchiveIssue pk=%s ('%s')",
                self.pk, self.title,
            )
        except ImportError:
            logger.warning(
                "django_q not installed — skipping async PDF ingestion for "
                "ArchiveIssue pk=%s. Run the ingest manually from the dashboard.",
                self.pk,
            )

    def _queue_page_prerender_task(self):
        """
        Queue a Django-Q2 async task to pre-render + disk-cache every page
        image for this issue (see the_librarian/page_cache.py), so the
        first reader to open the flip-book viewer gets pages served
        instantly instead of triggering a render for each one as they
        read. Same transaction.on_commit + django_q pattern as
        _queue_ingestion_task, and just as optional: if django_q isn't
        installed, pages simply render lazily on first view instead —
        slower for that first reader, but not broken.
        """
        try:
            from django.db import transaction
            from django_q.tasks import async_task
            _pk = self.pk
            transaction.on_commit(lambda: async_task(
                'the_librarian.tasks.async_prerender_issue_pages', _pk
            ))
            logger.info(
                "Queued page pre-render for ArchiveIssue pk=%s ('%s')",
                self.pk, self.title,
            )
        except ImportError:
            logger.warning(
                "django_q not installed — skipping page pre-render for "
                "ArchiveIssue pk=%s. Pages will render lazily on first view.",
                self.pk,
            )

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