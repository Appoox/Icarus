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
from django.contrib.postgres.search import SearchQuery, SearchRank
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
        "language": getattr(chunk, "language", "ml"),
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
        list of dicts with chunk metadata and similarity score.
        The list may be shorter than top_k if min_score filters some out.
    """
    query_embedding = embed_query(query_text)

    results = (
        DocumentChunk.objects
        .defer("embedding")                              # Avoid fetching large vectors into memory
        .annotate(distance=CosineDistance("embedding", query_embedding))
        .filter(distance__lte=1.0 - min_score)          # DB-level filtering
        .order_by("distance")
        .select_related("document", "article", "author")[:top_k]
    )

    output = []
    for chunk in results:
        score = round(1 - chunk.distance, 4)
        output.append(_format_chunk_result(chunk, score=score, search_type="similarity"))
    return output


def search_keyword(query_text: str, top_k: int = 5, min_score: float = 0.0001):
    """
    Search for chunks using PostgreSQL Full-Text Search via a pre-computed
    tsvector column (search_vector) backed by a GIN index.

    Returns:
        list of dicts (same format as search_similar).
    """
    # 'simple' config matches the generated search_vector field; important for Malayalam text
    query = SearchQuery(query_text, config="simple")

    results = (
        DocumentChunk.objects
        .defer("embedding")                    # Stop fetching massive vectors into memory
        .filter(search_vector=query)           # Uses the GIN index
        .annotate(rank=SearchRank(F("search_vector"), query))
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
                search_type="keyword",
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

    # 2. Apply RRF — track search_type separately to avoid mutating shared dicts
    rrf_scores = {}   # chunk_id -> combined RRF score
    chunk_map = {}    # chunk_id -> result dict
    search_types = {} # chunk_id -> search_type label

    for rank, res in enumerate(sim_results, 1):
        cid = res["chunk_id"]
        rrf_scores[cid] = rrf_scores.get(cid, 0) + 1.0 / (k + rank)
        chunk_map[cid] = res
        search_types[cid] = "similarity"

    for rank, res in enumerate(kw_results, 1):
        cid = res["chunk_id"]
        rrf_scores[cid] = rrf_scores.get(cid, 0) + 1.0 / (k + rank)
        chunk_map.setdefault(cid, res)
        search_types[cid] = "hybrid" if cid in search_types else "keyword"

    # 3. Sort by RRF score and build output
    sorted_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)[:top_k]

    final_output = []
    for cid in sorted_ids:
        res = chunk_map[cid].copy()  # copy to avoid mutating the cached result dict
        res["score"] = round(rrf_scores[cid], 6)
        res["search_type"] = search_types[cid]
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
        .defer("embedding")                              # Avoid fetching large vectors into memory
        .filter(document__filename=document_name)
        .annotate(distance=CosineDistance("embedding", query_embedding))
        .filter(distance__lte=1.0 - min_score)          # DB-level filtering, mirrors search_similar
        .order_by("distance")
        .select_related("document", "article", "author")[:top_k]
    )

    output = []
    for chunk in results:
        score = round(1 - chunk.distance, 4)
        output.append(_format_chunk_result(chunk, score=score, search_type="similarity"))
    return output