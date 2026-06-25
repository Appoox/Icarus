"""
OCR-based PDF ingestion pipeline for The Librarian.

Pipeline: PDF → images (pdf2image) → YOLO segmentation → Surya OCR
         → Semantic chunking → Embedding → pgvector storage

Each chunk retains its source page number so that ViewerJS can open
the PDF to the exact page.

Improvements applied
--------------------
#4  Per-page OCR results are written to a file cache keyed by
    (filename, content-hash).  A crashed or interrupted ingest resumes
    from the last completed page instead of re-OCR-ing the whole document.

#5  image_height is forwarded to custom_sort so its row-grouping tolerance
    scales with the scan resolution rather than using a fixed 5 px constant.

#8  The stop-signal mechanism combines a threading.Event (fast in-process
    signalling, checked between every page) with the original file-based
    flag (retained for cross-process / management-command use).

NEW ingest_archive_issue()
    Resolves the PDF from an ArchiveIssue model instance, ingests it via
    ingest_single_pdf(), then auto-links the resulting ArchiveDocument back
    to the ArchiveIssue.  Called by the async Q2 task wrapper in tasks.py.
"""
import os
import logging
import threading
from pathlib import Path

import numpy as np
from PIL import Image
from pdf2image import convert_from_path, pdfinfo_from_path

from django.conf import settings
from django.db import transaction

from the_librarian.models import ArchiveDocument, DocumentChunk
from the_librarian.services.embedder import embed_texts

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
# #4: Per-page OCR cache
# ---------------------------------------------------------------------------
import re
import regex
import hashlib

NOISE_SCRIPT_PATTERN = regex.compile(
    r'[^\p{Script=Malayalam}\p{Script=Latin}\p{Script=Common}\p{Script=Inherited}\s]+'
)

AD_SIGNALS = [
    r'\+91[\s\-]?\d{5}[\s\-]?\d{5}',
    r'CNRB\s*\d+',
    r'IFS\s*[Cc]ode',
    r'sasthragath[y]?@gmail',
    r'ksspmagazine@gmail',
    r'Price\s+Rs',
    r'Registered\.\s*No',
    r'www\.[a-z]+\.(com|in)',
    r'വരിസംഖ്യ.*രൂപ',
    r'ബാങ്കിൽ\s+പണ',
]

def _is_metadata_chunk(text: str) -> bool:
    hits = sum(1 for p in AD_SIGNALS if re.search(p, text))
    return hits >= 2


def _remove_noise_chars(text: str) -> str:
    return NOISE_SCRIPT_PATTERN.sub('', text)


def _fix_broken_malayalam_words(text: str) -> str:
    text = re.sub(r'([ഀ-ൿa-zA-Z])\s*[-—]\s*\n\s*([ഀ-ൿa-zA-Z])', r'\1\2', text)
    text = re.sub(r'([ഀ-ൿ])\s*\n\s*([ഀ-ൿ])', r'\1 \2', text)
    text = re.sub(r'([ഀ-ൿ])\s*\n\s*[—\-]\s*\n\s*([ഀ-ൿ])', r'\1 \2', text)
    text = re.sub(r'([ഀ-ൿ])\u00ad([ഀ-ൿ])', r'\1\2', text)
    return text


def _remove_noise_lines(text: str) -> str:
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned.append('')
            continue
        has_malayalam = bool(re.search(r'[ഀ-ൿ]', stripped))
        has_latin_word = bool(re.search(r'[a-zA-Z]{3,}', stripped))
        if not has_malayalam and not has_latin_word:
            continue
        cleaned.append(line)
    return '\n'.join(cleaned)


def _dedupe_repeated_lines(text: str) -> str:
    lines = text.split('\n')
    seen = set()
    unique = []
    for line in lines:
        key = re.sub(r'\s+', '', line).lower()
        if len(key) < 30:
            unique.append(line)
            continue
        if key not in seen:
            seen.add(key)
            unique.append(line)
    return '\n'.join(unique)


def _dedupe_repeated_paragraphs(text: str) -> str:
    paragraphs = re.split(r'\n{2,}', text)
    seen = set()
    unique = []
    for para in paragraphs:
        key = re.sub(r'\s+', '', para).lower()
        if len(key) < 20:
            unique.append(para)
            continue
        if key not in seen:
            seen.add(key)
            unique.append(para)
    return '\n\n'.join(unique)


def _remove_hallucinated_loops(text: str) -> str:
    word_pattern = r'(?P<phrase>(?:\S+\s+){0,9}\S+)(?:\s+(?P=phrase)){1,}'
    prev_text = None
    for _ in range(5):
        prev_text = text
        text = re.sub(word_pattern, r'\g<phrase>', text, flags=re.IGNORECASE)
        if text == prev_text:
            break
    char_pattern = r'(?P<syl>[a-zA-Zഀ-ൿ]{1,15}?)(?P=syl){3,}'
    text = re.sub(char_pattern, r'\g<syl>', text, flags=re.IGNORECASE)
    return text


def _remove_page_headers_footers(text: str) -> str:
    text = re.sub(r'\d*\s*ശാസ്ത്രഗതി\s*[\|]?\s*(?:ആഗസ്റ്റ്|ത്രഗസ്റ്റ്)\s*\d{4}', '', text)
    text = re.sub(r'(?:ആഗസ്റ്റ്|ത്രഗസ്റ്റ്)\s*\d{4}\s*[\|]?\s*ശാസ്ത്രഗതി\s*\d*', '', text)
    text = re.sub(
        r'SASTHRAGATHI\s*\n.*?AUGUST\s*\d{4}.*?\n.*?Price.*?\n',
        '', text, flags=re.DOTALL
    )
    return text


def preprocess_malayalam_pdf_text(text: str, chunk_id: int = None) -> str | None:
    if not isinstance(text, str):
        return None
    if _is_metadata_chunk(text):
        return None
    text = re.sub(r'\n', '', text)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\\overline\{[^\}]+\}', '', text)
    text = _remove_page_headers_footers(text)
    text = _fix_broken_malayalam_words(text)
    text = _remove_noise_chars(text)
    text = _remove_hallucinated_loops(text)
    text = re.sub(r'[-_.=~—]{3,}', ' ', text)
    text = _remove_noise_lines(text)
    text = _dedupe_repeated_lines(text)
    text = re.sub(r'[_—\-]*<[^>]+>[_—\-]*', ' ', text)
    text = re.sub(r' +', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()
    malayalam_chars = len(re.findall(r'[ഀ-ൿ]', text))
    if len(text) < 80 or malayalam_chars < 20:
        return None
    return text


def preprocess_chunks(rows, text_field="text", log_discarded=True):
    cleaned = []
    discarded = 0
    for row in rows:
        original_text = row.get(text_field, "")
        result = preprocess_malayalam_pdf_text(original_text, chunk_id=row.get("id"))
        if result is None:
            discarded += 1
            if log_discarded:
                preview = original_text[:60].replace('\n', ' ')
                print(f"[DISCARDED] id={row.get('id')} | {preview!r}")
            continue
        cleaned.append({**row, text_field: result})
    print(f"\nDone: {len(cleaned)} kept, {discarded} discarded out of {len(rows)} total")
    return cleaned


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
# Text chunking
# ---------------------------------------------------------------------------

def _chunk_pages(page_texts):
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from langchain_core.documents import Document

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=100,
        separators=["\n\n", "\n", " ", ""],
        is_separator_regex=False,
    )

    all_chunks = []
    global_index = 0

    for page_number, text in page_texts:
        if not text or not text.strip():
            continue

        cleaned_text = preprocess_malayalam_pdf_text(text)
        if cleaned_text is None:
            logger.info(f"Skipping page {page_number}: Discarded by preprocessor.")
            continue

        doc = Document(
            page_content=cleaned_text,
            metadata={"page_number": page_number},
        )

        try:
            chunks = splitter.split_documents([doc])
        except Exception as e:
            logger.warning(f"Chunking failed for page {page_number}: {e}")
            chunks = [doc]

        for chunk in chunks:
            chunk_text = chunk.page_content.strip()
            malayalam_chars = len(re.findall(r'[ഀ-ൿ]', chunk_text))
            if len(chunk_text) < 40 or malayalam_chars < 30:
                continue
            all_chunks.append({
                "chunk_text": chunk_text,
                "page_number": page_number,
                "chunk_index": global_index,
            })
            global_index += 1

    return all_chunks


# ---------------------------------------------------------------------------
# Main ingestion — single PDF
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
            # File is not under base (e.g. absolute path in a different tree).
            # Fall back to storing just the media-relative path if possible,
            # or the bare filename as a last resort.
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
# New: ingest from ArchiveIssue model
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
    ArchiveIssue = apps.get_model('the_librarian', 'ArchiveIssue')

    try:
        archive_issue = ArchiveIssue.objects.get(pk=archive_issue_pk)
    except ArchiveIssue.DoesNotExist:
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

    # Use MEDIA_ROOT as base so relative_path = "archive/filename.pdf"
    result = ingest_single_pdf(
        pdf_path,
        force=force,
        base_path=Path(settings.MEDIA_ROOT),
    )

    # Auto-link on success or skip (skip means already ingested → link if missing)
    if result["status"] in ("processed", "skipped"):
        try:
            doc = ArchiveDocument.objects.get(filename=pdf_path.name)
            if archive_issue.archive_document_id != doc.pk:
                ArchiveIssue.objects.filter(pk=archive_issue_pk).update(
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
    Kept for backward-compatibility with the ingest_archive management command.
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