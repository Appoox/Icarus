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

from django.db.models import F
from django.contrib.postgres.search import SearchVector, SearchQuery, SearchRank
from pgvector.django import CosineDistance

from the_librarian.models import DocumentChunk
from the_librarian.services.embedder import embed_query

logger = logging.getLogger(__name__)


def _format_chunk_result(chunk, score=None, search_type=None):
    """
    Unified formatter for search results across PDFs, Articles, and Authors.
    """
    res = {
        "chunk_id": chunk.id,
        "chunk_text": chunk.chunk_text,
        "score": score,
        "search_type": search_type,
        "language": getattr(chunk, 'language', 'ml'),
    }

    if chunk.document_id:
        res.update({
            "type": "pdf",
            "title": chunk.document.filename,
            "document_id": chunk.document.id,
            "file_path": chunk.document.file_path,
            "page_number": chunk.page_number,
        })
    elif chunk.article_id:
        res.update({
            "type": "article",
            "title": chunk.article.title,
            "url": chunk.article.url,
            "article_id": chunk.article.id,
        })
    elif chunk.author_id:
        res.update({
            "type": "author",
            "title": chunk.author.title,
            "url": chunk.author.url,
            "author_id": chunk.author.id,
        })
    else:
        res.update({
            "type": "unknown",
            "title": "Unknown Source",
        })
    
    return res


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
        .select_related("document", "article", "author")[:top_k]
    )

    output = []
    for chunk in results:
        score = round(1 - chunk.distance, 4)
        if score < min_score:
            continue
        output.append(_format_chunk_result(chunk, score=score, search_type="similarity"))
    return output


def search_keyword(query_text: str, top_k: int = 5, min_score: float = 0.0001):
    """
    Search for chunks using PostgreSQL Full-Text Search.

    Returns:
        list of dicts (same format as search_similar).
    """
    vector = SearchVector("chunk_text")
    query = SearchQuery(query_text)

    results = (
        DocumentChunk.objects.annotate(rank=SearchRank(vector, query))
        .filter(rank__gte=min_score)
        .order_by("-rank")
        .select_related("document", "article", "author")[:top_k]
    )

    output = []
    for chunk in results:
        output.append(
            _format_chunk_result(
                chunk, 
                score=round(float(chunk.rank), 4), 
                search_type="keyword"
            )
        )
    return output


def search_hybrid(query_text: str, top_k: int = 5, k: int = 60):
    """
    Hybrid search combining similarity and keyword results using
    Reciprocal Rank Fusion (RRF).

    Formula: score = Σ 1 / (k + rank)
    """
    # 1. Fetch more results than requested to allow for meaningful fusion
    fetch_k = top_k * 4

    sim_results = search_similar(query_text, top_k=fetch_k)
    kw_results = search_keyword(query_text, top_k=fetch_k)

    # 2. Apply RRF
    rrf_scores = {}  # chunk_id -> combined_score
    chunk_map = {}  # chunk_id -> result_dict

    for rank, res in enumerate(sim_results, 1):
        cid = res["chunk_id"]
        rrf_scores[cid] = rrf_scores.get(cid, 0) + 1.0 / (k + rank)
        chunk_map[cid] = res
        chunk_map[cid]["search_type"] = "similarity"

    for rank, res in enumerate(kw_results, 1):
        cid = res["chunk_id"]
        rrf_scores[cid] = rrf_scores.get(cid, 0) + 1.0 / (k + rank)
        if cid in chunk_map:
            chunk_map[cid]["search_type"] = "hybrid"
        else:
            chunk_map[cid] = res
            chunk_map[cid]["search_type"] = "keyword"

    # 3. Sort by RRF score
    sorted_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)[
        :top_k
    ]

    final_output = []
    for cid in sorted_ids:
        res = chunk_map[cid]
        res["score"] = round(rrf_scores[cid], 6)
        final_output.append(res)

    return final_output


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
        .select_related("document", "article", "author")[:top_k]
    )

    output = []
    for chunk in results:
        score = round(1 - chunk.distance, 4)
        if score < min_score:
            continue
        output.append(_format_chunk_result(chunk, score=score))
    return output