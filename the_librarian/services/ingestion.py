"""
OCR-based PDF ingestion pipeline for The Librarian.

Pipeline: PDF → images (pdf2image) → YOLO segmentation → Surya OCR
         → Semantic chunking → Embedding → pgvector storage

Each chunk retains its source page number so that ViewerJS can open
the PDF to the exact page.

Improvements applied
--------------------
#4  Per-page OCR results are written to a file cache keyed by
    (filename, mtime).  A crashed or interrupted ingest resumes from the
    last completed page instead of re-OCR-ing the whole document.

#5  image_height is forwarded to custom_sort so its row-grouping tolerance
    scales with the scan resolution rather than using a fixed 5 px constant.

#8  The stop-signal mechanism now combines a threading.Event (for fast
    in-process signalling, checked between every page) with the original
    file-based flag (retained for cross-process / management-command use).
"""
import os
import logging
import threading
from pathlib import Path

import numpy as np
from PIL import Image
from pdf2image import convert_from_path

from django.conf import settings
from django.db import transaction

from the_librarian.models import ArchiveDocument, DocumentChunk
from the_librarian.services.embedder import embed_texts

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# #8: Stop signal — threading.Event + file hybrid
# ---------------------------------------------------------------------------
# The Event gives immediate, zero-latency stop within the same process.
# The file is retained so that a separate web-request process (or a shell
# command) can also signal a stop, which the ingestion process will detect
# at its next page boundary.

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
    STOP_SIGNAL_FILE.touch()  # also write the file for cross-process visibility


# ---------------------------------------------------------------------------
# #4: Per-page OCR cache
# ---------------------------------------------------------------------------

import re

def preprocess_malayalam_pdf_text(text):
    if not isinstance(text, str):
        return text
        
    # 1. Remove HTML tags (including <br>, <b>, <math> etc.)
    text = re.sub(r'<[^>]+>', ' ', text)
    
    # 2. Fix broken Malayalam words caused by PDF line breaks
    # Matches a Malayalam char, a newline (with optional spaces), and another Malayalam char
    text = re.sub(r'([ഀ-ൿ])\s*\n\s*([ഀ-ൿ])', r'\1\2', text)
    
    # 3. Remove repeating punctuation/OCR noise (e.g., _____, ....., ----, ~~~)
    text = re.sub(r'[-_.=~]{3,}', ' ', text)
    
    # 4. Remove isolated garbage characters (bullets, random OCR symbols)
    # This removes lines that are just scattered single characters/symbols
    text = re.sub(r'(?m)^[\s\W0-9a-zA-Z]{1,5}$', '', text)
    
    # 5. Normalize whitespace (convert multiple spaces/newlines to a single space/newline)
    text = re.sub(r' +', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()
    
    return text

def _ocr_cache_dir(file_path: Path) -> Path:
    """
    Return the cache directory for a specific version of a PDF file.

    The directory name encodes the filename stem and the file's mtime so that
    any edit to the PDF (which changes mtime) automatically invalidates the
    old cache without manual intervention.
    """
    cache_root = Path(settings.BASE_DIR) / ".ocr_cache"
    mtime = int(file_path.stat().st_mtime)
    safe_stem = file_path.stem.replace(" ", "_")[:60]
    return cache_root / f"{safe_stem}_{mtime}"


def _load_cached_page(cache_dir: Path, page_num: int):
    """Return cached OCR text for *page_num*, or None if not yet cached."""
    page_file = cache_dir / f"page_{page_num}.txt"
    if page_file.exists():
        return page_file.read_text(encoding="utf-8")
    return None


def _save_cached_page(cache_dir: Path, page_num: int, text: str) -> None:
    """Persist the OCR result for *page_num* to the cache directory."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"page_{page_num}.txt").write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# OCR helpers
# ---------------------------------------------------------------------------

def _ocr_page(pil_image: Image.Image) -> str:
    """
    Run the full OCR pipeline on a single page image:
    skew correction → YOLO segmentation → Surya OCR per region.

    Returns the concatenated text for the entire page.

    #5: corrected image height is passed to custom_sort so that the
    row-grouping tolerance scales with the scan resolution.
    """
    from the_librarian.services.ocr_processing import (
        get_skew_corrected_image,
        process_image_for_ocr,
    )
    from the_librarian.services.yolo_segmentation import get_masks, custom_sort

    # Skew correction
    img_np = np.array(pil_image)
    corrected_np = get_skew_corrected_image(img_np)
    corrected_pil = Image.fromarray(corrected_np.astype("uint8"))

    # YOLO segmentation — models are loaded lazily inside get_masks()
    yolo_result = get_masks(corrected_pil)

    if yolo_result["status"] != 1:
        logger.warning("YOLO segmentation failed, running OCR on full page")
        return process_image_for_ocr(corrected_pil) or ""

    # #5: forward image height so tolerance scales with resolution
    sorted_boxes = custom_sort(
        yolo_result["boxes1"],
        image_height=corrected_pil.height,
    )

    page_text = ""
    for bbox in sorted_boxes:
        cropped_np = corrected_np[bbox[1]:bbox[3], bbox[0]:bbox[2]]
        if cropped_np.size == 0:
            continue
        cropped_pil = Image.fromarray(cropped_np.astype("uint8"))
        region_text = process_image_for_ocr(cropped_pil) or ""
        page_text += region_text + "\n"

    return preprocess_malayalam_pdf_text(page_text)


# ---------------------------------------------------------------------------
# Semantic chunking
# ---------------------------------------------------------------------------

def _chunk_pages(page_texts):
    """
    Semantically chunk the OCR'd text, preserving page number per chunk.

    Args:
        page_texts: list of (page_number, text) tuples.

    Returns:
        list of dicts: {chunk_text, page_number, chunk_index}

    Note on embedding cost (#3): SemanticChunker calls the embedder
    internally on individual sentences to find split points, then
    embed_texts() is called again on the resulting chunks.  Because chunks
    are merged sentences (not individual sentences), the two embedding sets
    rarely overlap.  The CachingEmbeddings wrapper in embedder.py ensures
    that any verbatim overlap *is* served from cache, and also provides
    batching (#10) for the final embed_texts() call.
    """
    from langchain_experimental.text_splitter import SemanticChunker
    from langchain_core.documents import Document
    from the_librarian.services.embedder import get_embedder

    # get_embedder() returns a CachingEmbeddings instance, so SemanticChunker
    # benefits from caching and batching automatically.
    chunker = SemanticChunker(get_embedder())

    all_chunks = []
    global_index = 0

    for page_number, text in page_texts:
        if not text or not text.strip():
            continue

        doc = Document(
            page_content=text,
            metadata={"page_number": page_number},
        )

        try:
            chunks = chunker.split_documents([doc])
        except Exception as e:
            logger.warning(f"Semantic chunking failed for page {page_number}: {e}")
            chunks = [doc]

        for chunk in chunks:
            all_chunks.append(
                {
                    "chunk_text": chunk.page_content,
                    "page_number": page_number,
                    "chunk_index": global_index,
                }
            )
            global_index += 1

    return all_chunks


# ---------------------------------------------------------------------------
# Main ingestion
# ---------------------------------------------------------------------------

def ingest_single_pdf(file_path, force=False):
    """
    Ingest a single PDF: OCR → chunk → embed → store in pgvector.

    Args:
        file_path: str or Path — absolute path to the PDF file.
        force: bool — if True, re-ingest even if already processed.

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
        # 1. Convert PDF pages to images
        images = convert_from_path(str(file_path))
        total_pages = len(images)
        logger.info(f"  {total_pages} pages found")

        # #4: resolve the per-document cache directory once
        cache_dir = _ocr_cache_dir(file_path)

        # 2. OCR each page (with per-page cache)
        page_texts = []
        for page_num, pil_image in enumerate(images, start=1):
            # #8: check stop signal between every page, not just between files
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

            # #4: serve from cache if this page was already processed
            cached = _load_cached_page(cache_dir, page_num)
            if cached is not None:
                logger.info(f"  Page {page_num}/{total_pages} (from cache)")
                page_texts.append((page_num, cached))
                continue

            logger.info(f"  OCR page {page_num}/{total_pages}")
            text = _ocr_page(pil_image)

            # #4: persist result so a restart can resume from here
            _save_cached_page(cache_dir, page_num, text)
            page_texts.append((page_num, text))

        # 3. Semantic chunking (preserves page numbers)
        chunks = _chunk_pages(page_texts)

        if not chunks:
            return {
                "filename": filename,
                "status": "error",
                "chunks_created": 0,
                "message": "No text extracted from PDF",
            }

        # 4. Embed all chunk texts — batching handled inside CachingEmbeddings
        chunk_texts = [c["chunk_text"] for c in chunks]
        embeddings = embed_texts(chunk_texts)

        # 5. Store in database
        relative_path = str(file_path.relative_to(settings.ARCHIVE_DIR))

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
    Scan settings.ARCHIVE_DIR for PDF files and ingest them.
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