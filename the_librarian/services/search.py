"""
Similarity search service for The Librarian.

Uses pgvector's cosine distance operator (<=>) to find the most
similar document chunks to a query.

Improvements applied
--------------------
#7  Both search functions now accept an optional ``min_score`` parameter
    (default 0.0, i.e. no filtering).  Results whose similarity score falls
    below the threshold are excluded from the returned list, preventing the
    caller from receiving chunks that are effectively unrelated to the query.
    A sensible starting value for Malayalam OCR content is around 0.35–0.45;
    tune it against your corpus.
"""
import logging

from pgvector.django import CosineDistance

from the_librarian.models import DocumentChunk
from the_librarian.services.embedder import embed_query

logger = logging.getLogger(__name__)


def search_similar(query_text: str, top_k: int = 5, min_score: float = 0.0):
    """
    Embed a query and find the most similar document chunks.

    Args:
        query_text: str — the natural-language query.
        top_k: int — maximum number of results to return.
        min_score: float — discard results with similarity below this value.
            Range is [0, 1]; 0 means no filtering (original behaviour).

    Returns:
        list of dicts: {
            chunk_text, document_name, file_path,
            page_number, score, chunk_id
        }
        The list may be shorter than top_k if min_score filters some out.
    """
    query_embedding = embed_query(query_text)

    results = (
        DocumentChunk.objects
        .annotate(distance=CosineDistance("embedding", query_embedding))
        .order_by("distance")
        .select_related("document")[:top_k]
    )

    output = []
    for chunk in results:
        score = round(1 - chunk.distance, 4)
        # #7: skip chunks below the caller's minimum relevance threshold
        if score < min_score:
            continue
        output.append(
            {
                "chunk_id": chunk.id,
                "chunk_text": chunk.chunk_text,
                "document_name": chunk.document.filename,
                "document_id": chunk.document.id,
                "file_path": chunk.document.file_path,
                "page_number": chunk.page_number,
                "score": score,
            }
        )
    return output


def search_by_document(
    document_name: str,
    query_text: str,
    top_k: int = 5,
    min_score: float = 0.0,
):
    """
    Search within a specific document only.

    Args:
        document_name: str — filename of the target document.
        query_text: str — the natural-language query.
        top_k: int — maximum number of results to return.
        min_score: float — discard results with similarity below this value.

    Returns:
        list of dicts (same format as search_similar).
    """
    query_embedding = embed_query(query_text)

    results = (
        DocumentChunk.objects
        .filter(document__filename=document_name)
        .annotate(distance=CosineDistance("embedding", query_embedding))
        .order_by("distance")
        .select_related("document")[:top_k]
    )

    output = []
    for chunk in results:
        score = round(1 - chunk.distance, 4)
        # #7: apply minimum score filter
        if score < min_score:
            continue
        output.append(
            {
                "chunk_id": chunk.id,
                "chunk_text": chunk.chunk_text,
                "document_name": chunk.document.filename,
                "document_id": chunk.document.id,
                "file_path": chunk.document.file_path,
                "page_number": chunk.page_number,
                "score": score,
            }
        )
    return output