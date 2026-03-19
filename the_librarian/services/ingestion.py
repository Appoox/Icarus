"""
OCR-based PDF ingestion pipeline for The Librarian.

Pipeline: PDF → images (pdf2image) → YOLO segmentation → Surya OCR
         → Semantic chunking → Embedding → pgvector storage

Each chunk retains its source page number so that ViewerJS can open
the PDF to the exact page.
"""
import os
import logging
from pathlib import Path

import numpy as np
from PIL import Image
from pdf2image import convert_from_path

from django.conf import settings
from django.db import transaction

from the_librarian.models import ArchiveDocument, DocumentChunk
from the_librarian.services.embedder import embed_texts

logger = logging.getLogger(__name__)


# ── OCR helpers ───────────────────────────────────────────────────────────

def _ocr_page(pil_image):
    """
    Run the full OCR pipeline on a single page image:
    skew correction → YOLO segmentation → Surya OCR per region.

    Returns the concatenated text for the entire page.
    """
    from the_librarian.services.ocr_processing import (
        get_skew_corrected_image,
        process_image_for_ocr,
    )
    from the_librarian.services.yolo_segmentation import (
        get_masks,
        custom_sort,
        general_model,
        image_model,
        configs,
    )

    # Skew correction
    img_np = np.array(pil_image)
    corrected_np = get_skew_corrected_image(img_np)
    corrected_pil = Image.fromarray(corrected_np.astype("uint8"))

    # YOLO segmentation to find text regions
    yolo_result = get_masks(corrected_pil, general_model, image_model, configs)

    if yolo_result["status"] != 1:
        # Fallback: run OCR on the full page without segmentation
        logger.warning("YOLO segmentation failed, running OCR on full page")
        return process_image_for_ocr(corrected_pil) or ""

    sorted_boxes = custom_sort(yolo_result["boxes1"])

    # OCR each detected region and concatenate
    page_text = ""
    for bbox in sorted_boxes:
        cropped_np = corrected_np[bbox[1]:bbox[3], bbox[0]:bbox[2]]
        if cropped_np.size == 0:
            continue
        cropped_pil = Image.fromarray(cropped_np.astype("uint8"))
        region_text = process_image_for_ocr(cropped_pil) or ""
        page_text += region_text + "\n"

    return page_text.strip()


# ── Semantic chunking ─────────────────────────────────────────────────────

def _chunk_pages(page_texts):
    """
    Semantically chunk the OCR'd text, preserving page number per chunk.

    Args:
        page_texts: list of (page_number, text) tuples.

    Returns:
        list of dicts: {chunk_text, page_number, chunk_index}
    """
    from langchain_experimental.text_splitter import SemanticChunker
    from the_librarian.services.embedder import get_embedder

    chunker = SemanticChunker(get_embedder())

    all_chunks = []
    global_index = 0

    for page_number, text in page_texts:
        if not text or not text.strip():
            continue

        # Create a LangChain-style document for the chunker
        from langchain_core.documents import Document
        doc = Document(
            page_content=text,
            metadata={"page_number": page_number},
        )

        try:
            chunks = chunker.split_documents([doc])
        except Exception as e:
            # If semantic chunking fails (e.g. too short), keep the whole page
            logger.warning(f"Semantic chunking failed for page {page_number}: {e}")
            chunks = [doc]

        for chunk in chunks:
            all_chunks.append({
                "chunk_text": chunk.page_content,
                "page_number": page_number,
                "chunk_index": global_index,
            })
            global_index += 1

    return all_chunks


# ── Main ingestion ────────────────────────────────────────────────────────

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

    # Check if already ingested
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

        # 2. OCR each page
        page_texts = []
        for page_num, pil_image in enumerate(images, start=1):
            logger.info(f"  OCR page {page_num}/{total_pages}")
            text = _ocr_page(pil_image)
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

        # 4. Embed all chunk texts in one batch
        chunk_texts = [c["chunk_text"] for c in chunks]
        embeddings = embed_texts(chunk_texts)

        # 5. Store in database
        relative_path = str(file_path.relative_to(settings.ARCHIVE_DIR))

        with transaction.atomic():
            # Remove old data if force re-ingesting
            if force:
                old_doc = ArchiveDocument.objects.filter(filename=filename).first()
                if old_doc:
                    old_doc.delete()

            archive_doc = ArchiveDocument.objects.create(
                filename=filename,
                file_path=relative_path,
                total_pages=total_pages,
            )

            chunk_objects = []
            for chunk_data, embedding in zip(chunks, embeddings):
                chunk_objects.append(
                    DocumentChunk(
                        document=archive_doc,
                        page_number=chunk_data["page_number"],
                        chunk_text=chunk_data["chunk_text"],
                        embedding=embedding,
                        chunk_index=chunk_data["chunk_index"],
                    )
                )

            DocumentChunk.objects.bulk_create(chunk_objects)

        logger.info(f"  Created {len(chunk_objects)} chunks for {filename}")
        return {
            "filename": filename,
            "status": "processed",
            "chunks_created": len(chunk_objects),
            "message": f"Successfully ingested {total_pages} pages → {len(chunk_objects)} chunks",
        }

    except Exception as e:
        logger.exception(f"Error ingesting {filename}")
        return {
            "filename": filename,
            "status": "error",
            "chunks_created": 0,
            "message": str(e),
        }


def ingest_archive(force=False):
    """
    Scan settings.ARCHIVE_DIR for PDF files and ingest any that
    haven't been processed yet.

    Args:
        force: bool — if True, re-ingest all PDFs.

    Returns:
        dict with keys: processed, skipped, errors (lists of result dicts)
    """
    archive_dir = Path(settings.ARCHIVE_DIR)
    if not archive_dir.exists():
        logger.error(f"Archive directory does not exist: {archive_dir}")
        return {"processed": [], "skipped": [], "errors": []}

    pdf_files = sorted(archive_dir.glob("*.pdf"))

    if not pdf_files:
        logger.info("No PDF files found in archive directory")
        return {"processed": [], "skipped": [], "errors": []}

    results = {"processed": [], "skipped": [], "errors": []}

    for pdf_path in pdf_files:
        result = ingest_single_pdf(pdf_path, force=force)
        results[result["status"]].append(result)

    return results
