from django.db import models
from django.conf import settings
from django.db.models import GeneratedField
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.contrib.postgres.indexes import GinIndex
from pgvector.django import VectorField, HnswIndex
from wagtail.admin.panels import FieldPanel, MultiFieldPanel
from wagtail.contrib.settings.models import BaseGenericSetting, register_setting

import hashlib
import secrets
import logging
from django.utils import timezone

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
    # ── Related Topic chunks ──────────────────────────────────────────
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
        help_text="Order of this chunk within the document/author/issue"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['document', 'chunk_index']
        verbose_name = "Document Chunk"
        verbose_name_plural = "Document Chunks"
        indexes = [
            models.Index(fields=['document', 'page_number']),
            GinIndex(fields=["search_vector"]),
            # Approximate-nearest-neighbor index backing every CosineDistance
            # query in services/search.py. Must be declared here 
            HnswIndex(
                name='chunk_embedding_hnsw',
                fields=['embedding'],
                m=16,
                ef_construction=64,
                opclasses=['vector_cosine_ops'],
            ),
        ]
        constraints = [
            # Makes a retried/duplicate submission from the remote worker
            # idempotent: update_or_create on (document, chunk_index) converges
            # to the same rows instead of ever duplicating them.
            models.UniqueConstraint(
                fields=['document', 'chunk_index'],
                condition=models.Q(document__isnull=False),
                name='unique_document_chunk_index',
            ),
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

    (year, month) is enforced unique at the DB layer — the same issue cannot
    be uploaded twice under two different filenames. Wagtail surfaces the
    constraint's violation_error_message directly in the snippet edit form
    via the model form's automatic validate_unique() call.
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
        constraints = [
            models.UniqueConstraint(
                fields=['year', 'month'],
                name='unique_archive_issue_year_month',
                violation_error_message=(
                    "An Archive Issue for this year and month already exists. "
                    "Each year–month combination can only be uploaded once — "
                    "edit the existing entry instead of creating a duplicate "
                    "under a different filename."
                ),
            ),
        ]

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


# ── Remote ingestion worker: API keys, kill-switch, access log ───────────

class LibrarianAPIKey(models.Model):
    """
    An API key for the remote OCR/embedding worker (a script running on a
    personal machine or any other off-box GPU host — see worker/ingest_worker.py).

    Fully managed from the "Remote Ingestion Worker" dashboard panel:
    created there (plaintext shown exactly once, never stored or retrievable
    again), listed there, revoked there. Multiple keys can be active at once
    (e.g. one per worker machine) so revoking one never affects the others.

    Only the SHA-256 hash of the raw key is stored — the key itself has
    256 bits of entropy from secrets.token_urlsafe(32), so an unsalted,
    unkeyed hash is the same standard practice used by GitHub/Stripe-style
    API tokens: the input is already unguessable, so a slow/salted hash adds
    nothing but lookup cost.
    """
    name = models.CharField(
        max_length=100,
        help_text="A label to identify this key, e.g. 'Home workstation' or 'Laptop worker'.",
    )
    key_hash = models.CharField(
        max_length=64, unique=True, editable=False,
        help_text="SHA-256 hash of the API key. The plaintext key is shown once at creation and never stored.",
    )
    key_prefix = models.CharField(
        max_length=16, editable=False,
        help_text="First few characters of the key, shown in the admin so keys can be told apart without exposing the full value.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )
    last_used_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Librarian API Key"
        verbose_name_plural = "Librarian API Keys"

    def __str__(self):
        status = "active" if self.is_active else "revoked"
        return f"{self.name} ({self.key_prefix}…, {status})"

    @staticmethod
    def hash_key(raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode()).hexdigest()

    @classmethod
    def generate(cls, name, created_by=None):
        """
        Creates a new key and returns (instance, raw_key). raw_key exists
        only in this return value — it is never persisted anywhere and
        cannot be recovered after the caller's response is sent.
        """
        raw_key = f"icrs_{secrets.token_urlsafe(32)}"
        instance = cls.objects.create(
            name=name,
            key_hash=cls.hash_key(raw_key),
            key_prefix=raw_key[:12],
            created_by=created_by,
        )
        return instance, raw_key

    @classmethod
    def authenticate(cls, raw_key: str):
        """Returns the matching active LibrarianAPIKey, or None."""
        if not raw_key:
            return None
        try:
            return cls.objects.get(key_hash=cls.hash_key(raw_key), is_active=True)
        except cls.DoesNotExist:
            return None

    def revoke(self, revoked_by=None):
        self.is_active = False
        self.revoked_at = timezone.now()
        self.revoked_by = revoked_by
        self.save(update_fields=['is_active', 'revoked_at', 'revoked_by'])


@register_setting
class LibrarianRemoteIngestSettings(BaseGenericSetting):
    """
    Kill-switch for the remote-worker API surface (the_librarian views:
    list_pending_for_worker, worker_download_pdf, submit_ingested_chunks).
    Those views 403 unconditionally unless `enabled` is True here — a valid
    API key is necessary but no longer sufficient on its own.

    Deliberately has NO automatic expiry: once an admin flips this on, it
    stays on until an admin flips it off again. Toggled exclusively from the
    "Remote Ingestion Worker" dashboard panel.
    """
    enabled = models.BooleanField(default=False)
    enabled_at = models.DateTimeField(null=True, blank=True)
    enabled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )
    pending_total_at_enable = models.PositiveIntegerField(
        default=0,
        help_text="Snapshot of un-ingested ArchiveIssues at the moment this was last enabled — "
                  "the denominator for the dashboard progress bar.",
    )

    panels = [
        FieldPanel('enabled', read_only=True),
        FieldPanel('enabled_at', read_only=True),
        FieldPanel('enabled_by', read_only=True),
        FieldPanel('pending_total_at_enable', read_only=True),
    ]

    class Meta:
        verbose_name = "Remote Ingestion Worker Settings"

    @property
    def is_currently_enabled(self):
        return self.enabled


class LibrarianWorkerAccessLog(models.Model):
    """
    Every request to the remote-worker API surface — successful or not —
    is logged here. This is both the security audit trail (which key/IP did
    what, and every failed auth attempt) and the data source for the
    dashboard's progress bars and recent-activity table.
    """
    EVENT_CHOICES = [
        ('auth_failed',      'Authentication Failed'),
        ('disabled',         'Rejected — Remote Ingestion Disabled'),
        ('pending_check',    'Listed Pending Issues'),
        ('download',         'Downloaded PDF'),
        ('submit_ok',        'Submitted Chunk Batch'),
        ('submit_rejected',  'Chunk Batch Rejected'),
    ]
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    remote_addr = models.GenericIPAddressField(null=True, blank=True)
    event = models.CharField(max_length=20, choices=EVENT_CHOICES)
    detail = models.CharField(max_length=255, blank=True)
    api_key = models.ForeignKey(
        LibrarianAPIKey, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='access_logs',
    )
    archive_issue = models.ForeignKey(
        'ArchiveIssue', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='worker_log_entries',
    )
    chunk_count = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Librarian Worker Access Log"
        verbose_name_plural = "Librarian Worker Access Logs"

    def __str__(self):
        return f"{self.get_event_display()} @ {self.created_at:%Y-%m-%d %H:%M} ({self.remote_addr or '—'})"