"""
Similarity search service for The Librarian.

Uses pgvector's cosine distance operator (<=>) to find the most
similar document chunks to a query.

Supports chunks from five sources: ArchiveDocument PDFs, Articles,
Literati authors, Issue editorials, and Related Topics.

Improvements applied
--------------------
#7  Both search functions accept an optional ``min_score`` parameter
    (default 0.0).  Results whose similarity score falls below the threshold
    are excluded from the returned list.
"""
import logging

from django.db.models import F, Q
from django.contrib.postgres.search import SearchQuery, SearchRank
from pgvector.django import CosineDistance

from the_librarian.models import DocumentChunk
from the_librarian.services.embedder import embed_query

logger = logging.getLogger(__name__)


def _format_chunk_result(chunk, score=None, search_type=None):
    """
    Unified formatter for search results across PDFs, Articles, Authors,
    Issue editorials, and Topics.
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
    elif chunk.issue_id:
        # Editorial content from an Issue page
        res.update({
            "type": "editorial",
            "title": f"{chunk.issue.title} — Editorial",
            "url": chunk.issue.url,
            "issue_id": chunk.issue.id,
        })
    elif chunk.topic_id:
        # Added handling for the new Topic chunk format
        res.update({
            "type": "topic",
            "title": chunk.topic.name,
            "topic_id": chunk.topic.id,
            "color": getattr(chunk.topic, 'color', '#000000'),
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
    """
    query_embedding = embed_query(query_text)

    results = (
        DocumentChunk.objects
        .defer("embedding")
        .annotate(distance=CosineDistance("embedding", query_embedding))
        .filter(distance__lte=1.0 - min_score)
        .filter(Q(article__isnull=True) | Q(article__live=True))
        .order_by("distance")
        .select_related("document", "article", "author", "issue", "topic")[:top_k]
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
    query = SearchQuery(query_text, config="simple")

    results = (
        DocumentChunk.objects
        .defer("embedding")
        .filter(search_vector=query)
        .annotate(rank=SearchRank(F("search_vector"), query))
        .filter(rank__gte=min_score)
        .filter(Q(article__isnull=True) | Q(article__live=True))
        .order_by("-rank")
        .select_related("document", "article", "author", "issue", "topic")[:top_k]
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
    fetch_k = top_k * 4

    sim_results = search_similar(query_text, top_k=fetch_k)
    kw_results = search_keyword(query_text, top_k=fetch_k)

    rrf_scores = {}
    chunk_map = {}
    search_types = {}

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

    sorted_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)[:top_k]

    final_output = []
    for cid in sorted_ids:
        res = chunk_map[cid].copy()
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
    """
    query_embedding = embed_query(query_text)

    results = (
        DocumentChunk.objects
        .defer("embedding")
        .filter(document__filename=document_name)
        .annotate(distance=CosineDistance("embedding", query_embedding))
        .filter(distance__lte=1.0 - min_score)
        .order_by("distance")
        .select_related("document", "article", "author", "issue", "topic")[:top_k]
    )

    output = []
    for chunk in results:
        score = round(1 - chunk.distance, 4)
        output.append(_format_chunk_result(chunk, score=score, search_type="similarity"))
    return output