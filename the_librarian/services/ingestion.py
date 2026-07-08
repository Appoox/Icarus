"""
OCR-based PDF ingestion pipeline for The Librarian.

Pipeline: PDF → images (pdf2image) → YOLO segmentation → Surya OCR
         → Semantic chunking → Embedding → pgvector storage

Each chunk retains its source page number so that ViewerJS can open
the PDF to the exact page.

The Malayalam text-cleanup and chunking logic lives in
the_librarian/services/text_cleanup.py — a Django-free module shared with
the standalone remote worker (worker/ingest_worker.py) so both sides of
that split can never silently drift apart.

NEW persist_ingested_batch()
    DB-write half of remote-worker submissions: takes an ArchiveIssue and a
    validated batch of VectorRecords (see services/vector_schema.py) and
    writes them to Postgres, keyed by a server-derived canonical filename
    rather than anything the worker claims.

Improvements retained from earlier revisions
---------------------------------------------
#4  Per-page OCR results are written to a file cache keyed by
    (filename, content-hash).  A crashed or interrupted ingest resumes
    from the last completed page instead of re-OCR-ing the whole document.

#8  The stop-signal mechanism combines a threading.Event (fast in-process
    signalling, checked between every page) with the original file-based
    flag (retained for cross-process / management-command use).

ingest_archive_issue()
    Resolves the PDF from an ArchiveIssue model instance, ingests it via
    ingest_single_pdf(), then auto-links the resulting ArchiveDocument back
    to the ArchiveIssue.  Called by the async Q2 task wrapper in tasks.py.
"""
import os
import logging
import threading
import hashlib
from pathlib import Path

from pdf2image import convert_from_path, pdfinfo_from_path

from django.conf import settings
from django.db import transaction

from the_librarian.models import ArchiveDocument, DocumentChunk, ArchiveIssue
from the_librarian.services.embedder import embed_texts
from the_librarian.services.text_cleanup import (
    preprocess_malayalam_pdf_text,
    preprocess_chunks,
    chunk_pages as _chunk_pages,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# #8: Stop signal — threading.Event + file hybrid
# ---------------------------------------------------------------------------
_STOP_EVENT = threading.Event()
STOP_SIGNAL_FILE = getattr(settings, "BASE_DIR") / ".stop_ingest"


def is_stop_requested() -> bool:
    """Return True if either the in-process event or the file flag is set."""
    return _STOP_EVENT.is_set() or STOP_SIGNAL_FILE.exists()


def clear_stop_signal() -> None:
    """Clear both the in-process event and the on-disk flag."""
    _STOP_EVENT.clear()
    if STOP_SIGNAL_FILE.exists():
        STOP_SIGNAL_FILE.unlink()


def request_stop() -> None:
    """Request ingestion to stop as soon as the current page finishes."""
    _STOP_EVENT.set()
    STOP_SIGNAL_FILE.touch()


# ---------------------------------------------------------------------------
# #4: Per-page OCR cache helpers
# ---------------------------------------------------------------------------

def _file_content_hash(file_path: Path) -> str:
    h = hashlib.sha256()
    file_size = file_path.stat().st_size
    with open(file_path, 'rb') as f:
        h.update(f.read(65536))
        if file_size > 65536:
            f.seek(-min(65536, file_size), 2)
            h.update(f.read())
    return h.hexdigest()[:16]


def _ocr_cache_dir(file_path: Path) -> Path:
    cache_root = Path(settings.BASE_DIR) / ".ocr_cache"
    content_hash = _file_content_hash(file_path)
    safe_stem = file_path.stem.replace(" ", "_")[:60]
    return cache_root / f"{safe_stem}_{content_hash}"


def _load_cached_page(cache_dir: Path, page_num: int):
    page_file = cache_dir / f"page_{page_num}.txt"
    if page_file.exists():
        return page_file.read_text(encoding="utf-8")
    return None


def _save_cached_page(cache_dir: Path, page_num: int, text: str) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"page_{page_num}.txt").write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Per-page OCR
# ---------------------------------------------------------------------------

def _ocr_page(pil_image):
    from the_librarian.services.ocr_processing import process_page
    return process_page(pil_image)


# ---------------------------------------------------------------------------
# Main ingestion — single PDF (local, in-process pipeline)
# ---------------------------------------------------------------------------

def ingest_single_pdf(file_path, force=False, base_path=None):
    """
    Ingest a single PDF: OCR → chunk → embed → store in pgvector.

    Args:
        file_path:  str or Path — absolute path to the PDF file.
        force:      bool — if True, re-ingest even if already processed.
        base_path:  optional Path — used to compute the relative path stored in
                    ArchiveDocument.file_path.  Defaults to settings.ARCHIVE_DIR.
                    Pass settings.MEDIA_ROOT when ingesting ArchiveIssue uploads
                    so the stored path is MEDIA_ROOT-relative (e.g.
                    "archive/filename.pdf") and serve_pdf() can locate the file.

    Returns:
        dict with keys: filename, status ('processed'|'skipped'|'error'),
                        chunks_created, message
    """
    file_path = Path(file_path)
    filename = file_path.name

    if not force and ArchiveDocument.objects.filter(filename=filename).exists():
        return {
            "filename": filename,
            "status": "skipped",
            "chunks_created": 0,
            "message": "Already ingested",
        }

    logger.info(f"Ingesting: {filename}")

    try:
        pdf_info = pdfinfo_from_path(str(file_path))
        total_pages = pdf_info['Pages']
        logger.info(f"  {total_pages} pages found")

        cache_dir = _ocr_cache_dir(file_path)

        page_texts = []
        for page_num in range(1, total_pages + 1):
            if is_stop_requested():
                logger.info(
                    f"Ingestion of {filename} stopped at page {page_num}/{total_pages}."
                )
                return {
                    "filename": filename,
                    "status": "error",
                    "chunks_created": 0,
                    "message": f"Stopped by user at page {page_num}/{total_pages}",
                }

            cached = _load_cached_page(cache_dir, page_num)
            if cached is not None:
                logger.info(f"  Page {page_num}/{total_pages} (from cache)")
                page_texts.append((page_num, cached))
                continue

            logger.info(f"  OCR page {page_num}/{total_pages}")
            page_images = convert_from_path(
                str(file_path),
                first_page=page_num,
                last_page=page_num,
                dpi=300,
            )
            pil_image = page_images[0]
            text = _ocr_page(pil_image)
            del page_images, pil_image

            _save_cached_page(cache_dir, page_num, text)
            page_texts.append((page_num, text))

        chunks = _chunk_pages(page_texts)

        if not chunks:
            return {
                "filename": filename,
                "status": "error",
                "chunks_created": 0,
                "message": "No text extracted from PDF",
            }

        chunk_texts = [c["chunk_text"] for c in chunks]
        embeddings = embed_texts(chunk_texts)

        # ── Compute file_path relative to the provided base ──────────────
        base = base_path or Path(settings.ARCHIVE_DIR)
        try:
            relative_path = str(file_path.relative_to(base))
        except ValueError:
            try:
                relative_path = str(file_path.relative_to(Path(settings.MEDIA_ROOT)))
            except ValueError:
                relative_path = filename
            logger.debug(
                "ingest_single_pdf: could not make '%s' relative to '%s'; "
                "stored as '%s'", file_path, base, relative_path
            )

        with transaction.atomic():
            if force:
                old_doc = ArchiveDocument.objects.filter(filename=filename).first()
                if old_doc:
                    old_doc.delete()

            archive_doc = ArchiveDocument.objects.create(
                filename=filename,
                file_path=relative_path,
                total_pages=total_pages,
            )

            chunk_objects = [
                DocumentChunk(
                    document=archive_doc,
                    page_number=chunk_data["page_number"],
                    chunk_text=chunk_data["chunk_text"],
                    embedding=embedding,
                    chunk_index=chunk_data["chunk_index"],
                )
                for chunk_data, embedding in zip(chunks, embeddings)
            ]

            DocumentChunk.objects.bulk_create(chunk_objects)

        logger.info(f"  Created {len(chunk_objects)} chunks for {filename}")
        return {
            "filename": filename,
            "status": "processed",
            "chunks_created": len(chunk_objects),
            "message": (
                f"Successfully ingested {total_pages} pages → {len(chunk_objects)} chunks"
            ),
        }

    except Exception as e:
        logger.exception(f"Error ingesting {filename}")
        return {
            "filename": filename,
            "status": "error",
            "chunks_created": 0,
            "message": str(e),
        }


# ---------------------------------------------------------------------------
# Ingest from ArchiveIssue model (local, in-process pipeline)
# ---------------------------------------------------------------------------

def ingest_archive_issue(archive_issue_pk, force=False):
    """
    Ingest the PDF attached to a single ArchiveIssue.

    Resolves the PDF from ArchiveIssue.pdf_file, calls ingest_single_pdf()
    with MEDIA_ROOT as the base path (so the stored relative path is
    MEDIA_ROOT-relative, e.g. "archive/filename.pdf"), then auto-links the
    resulting ArchiveDocument back to the ArchiveIssue via the
    archive_document FK.

    Args:
        archive_issue_pk: int — PK of the ArchiveIssue to ingest.
        force:            bool — if True, re-ingest even if already processed.

    Returns:
        dict matching the ingest_single_pdf() return format.
    """
    from django.apps import apps
    ArchiveIssueModel = apps.get_model('the_librarian', 'ArchiveIssue')

    try:
        archive_issue = ArchiveIssueModel.objects.get(pk=archive_issue_pk)
    except ArchiveIssueModel.DoesNotExist:
        msg = f"ArchiveIssue pk={archive_issue_pk} not found"
        logger.warning(msg)
        return {
            "filename": f"ArchiveIssue pk={archive_issue_pk}",
            "status": "error",
            "chunks_created": 0,
            "message": msg,
        }

    if not archive_issue.pdf_file:
        return {
            "filename": archive_issue.title,
            "status": "skipped",
            "chunks_created": 0,
            "message": "No PDF file attached to this ArchiveIssue",
        }

    pdf_path = Path(archive_issue.pdf_file.path)

    result = ingest_single_pdf(
        pdf_path,
        force=force,
        base_path=Path(settings.MEDIA_ROOT),
    )

    if result["status"] in ("processed", "skipped"):
        try:
            doc = ArchiveDocument.objects.get(filename=pdf_path.name)
            if archive_issue.archive_document_id != doc.pk:
                ArchiveIssueModel.objects.filter(pk=archive_issue_pk).update(
                    archive_document=doc
                )
                logger.info(
                    "Linked ArchiveIssue pk=%s to ArchiveDocument '%s'",
                    archive_issue_pk, pdf_path.name,
                )
        except ArchiveDocument.DoesNotExist:
            logger.warning(
                "ArchiveDocument for '%s' not found after ingestion", pdf_path.name
            )

    return result


# ---------------------------------------------------------------------------
# Filesystem-based batch helpers (unchanged, kept for management command)
# ---------------------------------------------------------------------------

def get_pending_pdfs(force=False):
    """Return a list of PDF filenames in ARCHIVE_DIR that need ingestion."""
    archive_dir = Path(settings.ARCHIVE_DIR)
    if not archive_dir.exists():
        return []
    pdf_files = sorted(archive_dir.glob("*.pdf"))
    if force:
        return [f.name for f in pdf_files]
    already_ingested = set(ArchiveDocument.objects.values_list("filename", flat=True))
    return [f.name for f in pdf_files if f.name not in already_ingested]


def ingest_archive(force=False, filename=None):
    """
    Scan settings.ARCHIVE_DIR for PDF files and ingest them synchronously.
    If filename is provided, only ingest that specific file.
    """
    archive_dir = Path(settings.ARCHIVE_DIR)
    if not archive_dir.exists():
        logger.error(f"Archive directory does not exist: {archive_dir}")
        return {"processed": [], "skipped": [], "errors": []}

    if filename:
        pdf_files = [archive_dir / filename]
    else:
        pdf_files = sorted(archive_dir.glob("*.pdf"))

    if not pdf_files:
        logger.info("No PDF files found in archive directory")
        return {"processed": [], "skipped": [], "errors": []}

    results: dict[str, list] = {"processed": [], "skipped": [], "errors": []}

    for pdf_path in pdf_files:
        if is_stop_requested():
            logger.info("Ingestion stopped by user request.")
            break
        result = ingest_single_pdf(pdf_path, force=force)
        key = "errors" if result["status"] == "error" else result["status"]
        results[key].append(result)

    return results


# ---------------------------------------------------------------------------
# Remote worker submission — batched, idempotent DB write
# ---------------------------------------------------------------------------

def persist_ingested_batch(*, archive_issue, batch_index, is_final, total_pages, records):
    """
    Persists one batch of VectorRecords (see services/vector_schema.py) for
    archive_issue's document.

    Identified by archive_issue's own (year, month) via a canonical,
    server-derived filename — never by anything the worker claims in the
    payload — so a submission can never overwrite an unrelated document.

    Idempotent per chunk: update_or_create keyed on (document, chunk_index)
    means a retried batch (e.g. the worker isn't sure a POST landed after a
    dropped connection) converges to the same end state instead of
    duplicating rows — backed by DocumentChunk's unique_document_chunk_index
    constraint.

    batch_index == 0 clears any existing chunks for this document first,
    so a re-run starts clean; later batches only add/update.
    """
    canonical_filename = (
        f"archive_issue_{archive_issue.pk}_{archive_issue.year}_{archive_issue.month:02d}.pdf"
    )
    relative_path = f"archive/{canonical_filename}"

    with transaction.atomic():
        archive_doc, created = ArchiveDocument.objects.get_or_create(
            filename=canonical_filename,
            defaults={"file_path": relative_path, "total_pages": total_pages},
        )
        if not created:
            archive_doc.total_pages = total_pages
            archive_doc.save(update_fields=["total_pages"])

        if batch_index == 0:
            DocumentChunk.objects.filter(document=archive_doc).delete()

        for record in records:
            DocumentChunk.objects.update_or_create(
                document=archive_doc,
                chunk_index=record.chunk_index,
                defaults={
                    "page_number": record.page_number,
                    "chunk_text": record.chunk_text,
                    "embedding": record.embedding,
                },
            )

        if is_final:
            ArchiveIssue.objects.filter(pk=archive_issue.pk).update(archive_document=archive_doc)

    return archive_doc