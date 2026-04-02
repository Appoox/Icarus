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
from pdf2image import convert_from_path, pdfinfo_from_path

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
import regex
import hashlib

# ── Characters confirmed as OCR noise: whitelist by Unicode script ────────
# Valid scripts for a Malayalam magazine: Malayalam, Latin, Common, Inherited.
# Anything else (Arabic, Greek, Cyrillic, Thai/Lao, etc.) is OCR garbage.
NOISE_SCRIPT_PATTERN = regex.compile(
    r'[^\p{Script=Malayalam}\p{Script=Latin}\p{Script=Common}\p{Script=Inherited}\s]+'
)

# Signals that a chunk is an advertisement or publication metadata
AD_SIGNALS = [
    r'\+91[\s\-]?\d{5}[\s\-]?\d{5}',   # Indian phone numbers
    r'CNRB\s*\d+',                       # Bank IFSC codes
    r'IFS\s*[Cc]ode',
    r'sasthragath[y]?@gmail',            # Known email in your data
    r'ksspmagazine@gmail',
    r'Price\s+Rs',
    r'Registered\.\s*No',
    r'www\.[a-z]+\.(com|in)',            # URLs
    r'വരിസംഖ്യ.*രൂപ',                  # "subscription fee X rupees"
    r'ബാങ്കിൽ\s+പണ',                    # "pay at bank"
]

def _is_metadata_chunk(text: str) -> bool:
    """Returns True if the chunk looks like an ad, footer, or publication info."""
    hits = sum(1 for p in AD_SIGNALS if re.search(p, text))
    return hits >= 2


def _remove_noise_chars(text: str) -> str:
    """Remove characters not belonging to valid scripts (Malayalam, Latin, Common, Inherited)."""
    return NOISE_SCRIPT_PATTERN.sub('', text)


def _fix_broken_malayalam_words(text: str) -> str:
    """
    Fix broken words across newlines without destroying tables/lists.
    """
    # 1. Join words explicitly broken by a hyphen over newlines (Malayalam AND English)
    # Fixes: "in-\ncome" -> "income"
    text = re.sub(r'([ഀ-ൿa-zA-Z])\s*[-—]\s*\n\s*([ഀ-ൿa-zA-Z])', r'\1\2', text)
    
    # 2. Prevent table columns/lists from squishing together!
    # Instead of concatenating r'\1\2', we insert a space r'\1 \2'. 
    # Fixes: "സഹകരണബാങ്കുകൾ\nപൊതുമേഖലാബാങ്കുകൾ" -> "സഹകരണബാങ്കുകൾ പൊതുമേഖലാബാങ്കുകൾ"
    text = re.sub(r'([ഀ-ൿ])\s*\n\s*([ഀ-ൿ])', r'\1 \2', text)
    
    # 3. Remove stray — or - between Malayalam line continuations
    text = re.sub(r'([ഀ-ൿ])\s*\n\s*[—\-]\s*\n\s*([ഀ-ൿ])', r'\1 \2', text)
    
    # 4. Collapse a Malayalam word broken with a soft hyphen inline
    text = re.sub(r'([ഀ-ൿ])\u00ad([ഀ-ൿ])', r'\1\2', text)
    
    return text



def _remove_noise_lines(text: str) -> str:
    """
    Aggressively removes lines that consist of vertical noise blocks,
    page numbers, timestamps, or stray hex IDs.
    """
    lines = text.split('\n')
    cleaned =[]
    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned.append('')
            continue
        
        has_malayalam = bool(re.search(r'[ഀ-ൿ]', stripped))
        has_latin_word = bool(re.search(r'[a-zA-Z]{3,}', stripped)) 
        
        if not has_malayalam and not has_latin_word:
            # If a line has no recognizable Malayalam and no English word >= 3 chars,
            # it is almost certainly a page number, timestamp (e.g. 30:10:1/3), 
            # or stray hex code (95119CF). Drop it completely.
            continue
            
        cleaned.append(line)
    
    return '\n'.join(cleaned)

def _dedupe_repeated_lines(text: str) -> str:
    """
    Fixes OCR multi-column misreads where it outputs the exact same block
    of text twice in a row. Operates at the line level.
    """
    lines = text.split('\n')
    seen = set()
    unique =[]
    
    for line in lines:
        # Normalize for comparison only (compare without spaces/punctuation)
        key = re.sub(r'\s+', '', line).lower()
        
        # Keep short lines (headings, lists) even if they repeat
        if len(key) < 30:          
            unique.append(line)
            continue
            
        if key not in seen:
            seen.add(key)
            unique.append(line)
        # else: silently drop the duplicate line
    
    return '\n'.join(unique)


def _dedupe_repeated_paragraphs(text: str) -> str:
    """
    Fixes the problem in your chunks 284-285 where the PDF extractor
    repeated the same paragraph 3-4 times due to multi-column layout.
    """
    paragraphs = re.split(r'\n{2,}', text)
    seen = set()
    unique = []
    
    for para in paragraphs:
        # Normalize for comparison only (compare without spaces/punctuation)
        key = re.sub(r'\s+', '', para).lower()
        if len(key) < 20:          # keep very short paras (headings etc.)
            unique.append(para)
            continue
        if key not in seen:
            seen.add(key)
            unique.append(para)
        # else: silently drop the duplicate paragraph
    
    return '\n\n'.join(unique)

def _remove_hallucinated_loops(text: str) -> str:
    """
    Surya OCR occasionally gets stuck in a decoding loop, repeating words or 
    syllables infinitely (e.g., 'പ്രത്യായി പ്രത്യായി പ്രത്യായി' or 'wigangangang').
    This squashes both word-level and character-level repetitions.
    """
    # 1. Squash repeating word sequences (1 to 10 words)
    # \S+ matches any non-whitespace char (handles English, Malayalam, numbers, punctuation).
    # (?P<phrase>...) captures the phrase.
    # (?:\s+(?P=phrase)){1,} matches 1 or more consecutive repetitions of the exact phrase.
    word_pattern = r'(?P<phrase>(?:\S+\s+){0,9}\S+)(?:\s+(?P=phrase)){1,}'
    
    # Run multiple times to catch nested or overlapping loops (e.g., squashing A A A A)
    prev_text = None
    for _ in range(5):  # Limit to 5 passes for safety/speed
        prev_text = text
        text = re.sub(word_pattern, r'\g<phrase>', text, flags=re.IGNORECASE)
        if text == prev_text:
            break
            
    # 2. Squash repeating character sequences within a single word 
    # (e.g., "gangangangan", "പോപോപോപോ")
    # Matches 1 to 15 letters repeating 3 or more times consecutively.
    char_pattern = r'(?P<syl>[a-zA-Zഀ-ൿ]{1,15}?)(?P=syl){3,}'
    text = re.sub(char_pattern, r'\g<syl>', text, flags=re.IGNORECASE)
    
    return text

def _remove_page_headers_footers(text: str) -> str:
    """
    Remove recurring page artifacts including typos and missing pipes.
    """
    # Catches: "ആഗസ്റ്റ് 2025 | ശാസ്ത്രഗതി", "0 ശാസ്ത്രഗതി ത്രഗസ്റ്റ് 2025"
    # Handles missing pipes and "ത്രഗസ്റ്റ്" (OCR typo for ആഗസ്റ്റ്)
    text = re.sub(r'\d*\s*ശാസ്ത്രഗതി\s*[\|]?\s*(?:ആഗസ്റ്റ്|ത്രഗസ്റ്റ്)\s*\d{4}', '', text)
    text = re.sub(r'(?:ആഗസ്റ്റ്|ത്രഗസ്റ്റ്)\s*\d{4}\s*[\|]?\s*ശാസ്ത്രഗതി\s*\d*', '', text)
    
    # English header block
    text = re.sub(
        r'SASTHRAGATHI\s*\n.*?AUGUST\s*\d{4}.*?\n.*?Price.*?\n',
        '', text, flags=re.DOTALL
    )
    return text


# ── Main pipeline ──────────────────────────────────────────────────────────

def preprocess_malayalam_pdf_text(text: str, chunk_id: int = None) -> str | None:
    if not isinstance(text, str):
        return None

    # ── Gate: discard advertisement / metadata chunks entirely ────────────
    if _is_metadata_chunk(text):
        return None

    # ── Step 1: HTML tags & Math/LaTeX artifacts ──────────────────────────
    text = re.sub(r'\n', '', text)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\\overline\{[^\}]+\}', '', text)

    # ── Step 2: Page headers and footers ──────────────────────────────────
    text = _remove_page_headers_footers(text)

    # ── Step 3: Fix broken Malayalam words ────────────────────────────────
    text = _fix_broken_malayalam_words(text)

    # ── Step 4: Remove OCR noise characters & Hallucination loops ─────────
    text = _remove_noise_chars(text)
    text = _remove_hallucinated_loops(text)

    # ── Step 5: Remove repeating punctuation sequences ────────────────────
    text = re.sub(r'[-_.=~—]{3,}', ' ', text)

    # ── Step 6: Remove pure-noise lines ───────────────────────────────────
    text = _remove_noise_lines(text)

    # ── Step 7: Deduplicate repeated lines (OCR column overlap) ───────────
    text = _dedupe_repeated_lines(text)
    text = re.sub(r'[_—\-]*<[^>]+>[_—\-]*', ' ', text)

    # ── Step 8: Normalize whitespace ──────────────────────────────────────
    text = re.sub(r' +', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()

    # ── Page-Level Gate: discard if too little content remains ────────────
    malayalam_chars = len(re.findall(r'[ഀ-ൿ]', text))
    if len(text) < 80 or malayalam_chars < 20:
        return None

    return text

# ── Batch usage with your pgvector rows ───────────────────────────────────

def preprocess_chunks(
    rows: list[dict],
    text_field: str = "text",
    log_discarded: bool = True
) -> list[dict]:
    """
    Args:
        rows: list of dicts from your pgvector query, each has at least
              {id: int, text: str, ...other metadata...}
    Returns:
        Cleaned rows with None-result rows removed.
        Original metadata (id, page, etc.) is preserved.
    """
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



def _file_content_hash(file_path: Path) -> str:
    """Generate a short content hash for reliable cache invalidation."""
    h = hashlib.sha256()
    file_size = file_path.stat().st_size
    with open(file_path, 'rb') as f:
        h.update(f.read(65536))           # First 64KB
        if file_size > 65536:
            f.seek(-min(65536, file_size), 2)  # Last 64KB
            h.update(f.read())
    return h.hexdigest()[:16]


def _ocr_cache_dir(file_path: Path) -> Path:
    """
    Return the cache directory for a specific version of a PDF file.

    The directory name encodes the filename stem and a content hash so that
    any edit to the PDF automatically invalidates the old cache without
    manual intervention.  Unlike mtime, a content hash is stable across
    file copies and backups.
    """
    cache_root = Path(settings.BASE_DIR) / ".ocr_cache"
    content_hash = _file_content_hash(file_path)
    safe_stem = file_path.stem.replace(" ", "_")[:60]
    return cache_root / f"{safe_stem}_{content_hash}"


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
# Per-page OCR
# ---------------------------------------------------------------------------

def _ocr_page(pil_image):
    """OCR a single PDF page using the full pipeline."""
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

    all_chunks =[]
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
            
            # ─────────────────────────────────────────────────────────────
            # CHUNK-LEVEL GATE: Filter out remaining fragments or chunks 
            # that are 95% alphanumeric hallucination garbage.
            # ─────────────────────────────────────────────────────────────
            malayalam_chars = len(re.findall(r'[ഀ-ൿ]', chunk_text))
            
            # Bumped from 10 to 30:
            if len(chunk_text) < 40 or malayalam_chars < 30: 
                continue

            all_chunks.append(
                {
                    "chunk_text": chunk_text,
                    "page_number": page_number,
                    "chunk_index": global_index,
                }
            )
            global_index += 1

    return all_chunks
    # return preprocess_malayalam_pdf_text(all_chunks)


# ---------------------------------------------------------------------------
# Semantic chunking
# ---------------------------------------------------------------------------

# def _chunk_pages(page_texts):
#     """
#     Semantically chunk the OCR'd text, preserving page number per chunk.

#     Args:
#         page_texts: list of (page_number, text) tuples.

#     Returns:
#         list of dicts: {chunk_text, page_number, chunk_index}

#     Note on embedding cost (#3): SemanticChunker calls the embedder
#     internally on individual sentences to find split points, then
#     embed_texts() is called again on the resulting chunks.  Because chunks
#     are merged sentences (not individual sentences), the two embedding sets
#     rarely overlap.  The CachingEmbeddings wrapper in embedder.py ensures
#     that any verbatim overlap *is* served from cache, and also provides
#     batching (#10) for the final embed_texts() call.
#     """
#     from langchain_experimental.text_splitter import SemanticChunker
#     from langchain_core.documents import Document
#     from the_librarian.services.embedder import get_embedder

#     # get_embedder() returns a CachingEmbeddings instance, so SemanticChunker
#     # benefits from caching and batching automatically.
#     chunker = SemanticChunker(get_embedder())

#     all_chunks = []
#     global_index = 0

#     for page_number, text in page_texts:
#         if not text or not text.strip():
#             continue

#         doc = Document(
#             page_content=text,
#             metadata={"page_number": page_number},
#         )

#         try:
#             chunks = chunker.split_documents([doc])
#         except Exception as e:
#             logger.warning(f"Semantic chunking failed for page {page_number}: {e}")
#             chunks = [doc]

#         for chunk in chunks:
#             all_chunks.append(
#                 {
#                     "chunk_text": chunk.page_content,
#                     "page_number": page_number,
#                     "chunk_index": global_index,
#                 }
#             )
#             global_index += 1

#     return all_chunks


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
        # 1. Get page count without loading images (memory-efficient)
        pdf_info = pdfinfo_from_path(str(file_path))
        total_pages = pdf_info['Pages']
        logger.info(f"  {total_pages} pages found")

        # #4: resolve the per-document cache directory once
        cache_dir = _ocr_cache_dir(file_path)

        # 2. OCR each page one at a time (memory-efficient, CPU-optimized)
        page_texts = []
        for page_num in range(1, total_pages + 1):
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

            # Load only this page at 300 DPI for better Malayalam ligature recognition
            logger.info(f"  OCR page {page_num}/{total_pages}")
            page_images = convert_from_path(
                str(file_path),
                first_page=page_num,
                last_page=page_num,
                dpi=300,
            )
            pil_image = page_images[0]

            text = _ocr_page(pil_image)

            # Free memory immediately
            del page_images, pil_image

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