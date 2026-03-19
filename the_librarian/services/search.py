"""
Similarity search service for The Librarian.

Uses pgvector's cosine distance operator (<=>) to find the most
similar document chunks to a query.
"""
import logging

from pgvector.django import CosineDistance

from the_librarian.models import DocumentChunk
from the_librarian.services.embedder import embed_query

logger = logging.getLogger(__name__)


def search_similar(query_text, top_k=5):
    """
    Embed a query and find the most similar document chunks.

    Args:
        query_text: str — the natural-language query.
        top_k: int — number of results to return.

    Returns:
        list of dicts: {
            chunk_text, document_name, file_path,
            page_number, score, chunk_id
        }
    """
    query_embedding = embed_query(query_text)

    results = (
        DocumentChunk.objects
        .annotate(distance=CosineDistance("embedding", query_embedding))
        .order_by("distance")
        .select_related("document")[:top_k]
    )

    return [
        {
            "chunk_id": chunk.id,
            "chunk_text": chunk.chunk_text,
            "document_name": chunk.document.filename,
            "document_id": chunk.document.id,
            "file_path": chunk.document.file_path,
            "page_number": chunk.page_number,
            "score": round(1 - chunk.distance, 4),  # Convert distance to similarity
        }
        for chunk in results
    ]


def search_by_document(document_name, query_text, top_k=5):
    """
    Search within a specific document only.

    Args:
        document_name: str — filename of the target document.
        query_text: str — the natural-language query.
        top_k: int — number of results to return.

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

    return [
        {
            "chunk_id": chunk.id,
            "chunk_text": chunk.chunk_text,
            "document_name": chunk.document.filename,
            "document_id": chunk.document.id,
            "file_path": chunk.document.file_path,
            "page_number": chunk.page_number,
            "score": round(1 - chunk.distance, 4),
        }
        for chunk in results
    ]
