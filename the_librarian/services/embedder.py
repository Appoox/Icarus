"""
Swappable embedder service for The Librarian.

Reads LIBRARIAN_EMBEDDER_TYPE and LIBRARIAN_EMBEDDING_MODEL from Django settings.
The embedder is cached as a module-level singleton to avoid reloading the model
on every call.

To swap models, change EMBEDDING_MODEL_NAME and EMBEDDING_DIM in your .env file.

Improvements applied
--------------------
#3  CachingEmbeddings wraps the base embedder so that any text embedded during
    SemanticChunker's internal split-point computation is served from cache if
    it appears verbatim as a final chunk, avoiding a redundant model call.

#10 embed_documents processes texts in batches of EMBED_BATCH_SIZE (default 64)
    to prevent OOM errors and keep inference latency predictable when thousands
    of chunks are embedded at once.

#11 embed_query now has its own bounded LRU cache (a separate dict from the
    document cache, since "query: " and "passage: " prefixes mean the same
    raw text embeds to a different vector depending on which path it went
    through). Repeated searches — including the header search-as-you-type box
    re-hitting a term someone already searched, or two readers searching the
    same trending term — skip the model call entirely. Bounded to
    _QUERY_CACHE_MAX_SIZE entries with LRU eviction, since query text is
    arbitrary user input (much higher cardinality than the fixed document
    corpus) and would otherwise grow unbounded for the life of the worker
    process.
"""

import os
# Force CPU usage and disable GPU detection to prevent meta-tensor loading issues (#77)
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ["ACCELERATE_USE_CPU"] = "true"

from collections import OrderedDict

from django.conf import settings

batch_size = settings.LIBRARIAN_EMBED_BATCH_SIZE
_embedder_instance = None

# Max distinct queries kept in the query-embedding LRU cache per worker process.
_QUERY_CACHE_MAX_SIZE = 2048


# ---------------------------------------------------------------------------
# #3 + #10 + #11: Caching + batching wrapper
# ---------------------------------------------------------------------------

class CachingEmbeddings:
    """
    LangChain-compatible embeddings wrapper that:

    - Caches embed_documents results in an in-memory dict so identical texts
      are never sent to the model twice within the same process lifetime.
    - Processes texts in batches of EMBED_BATCH_SIZE to avoid OOM errors on
      large documents (#10).
    - Caches embed_query results in a separate, bounded LRU dict, since query
      text is high-cardinality user input rather than the fixed chunk corpus (#11).

    The document cache is intentionally unbounded and process-scoped. For very
    large archives this trades memory for speed; adjust EMBED_BATCH_SIZE or add
    an LRU eviction policy if memory becomes a concern. The query cache is
    bounded from the start for exactly that reason.
    """

    def __init__(self, base_embedder):
        self._base = base_embedder
        self._cache: dict[str, list[float]] = {}
        # OrderedDict gives us cheap "move to end on access" for LRU behaviour.
        self._query_cache: "OrderedDict[str, list[float]]" = OrderedDict()

    # LangChain's SemanticChunker calls embed_documents internally.
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        uncached = [t for t in texts if t not in self._cache]

        if uncached:
            new_embeddings: list[list[float]] = []
            # #10: iterate in fixed-size batches
            for i in range(0, len(uncached), batch_size):
                batch = uncached[i : i + batch_size]
                prefixed_batch = ["passage: " + t for t in batch]
                new_embeddings.extend(self._base.embed_documents(prefixed_batch))
            for text, emb in zip(uncached, new_embeddings):
                self._cache[text] = emb

        return [self._cache[t] for t in texts]

    # embed_query uses a separate asymmetric path — now cached (#11), bounded
    # and keyed independently from the document cache above so a chunk of
    # text that also happens to be searched-for never collides on a key.
    def embed_query(self, text: str) -> list[float]:
        cached = self._query_cache.get(text)
        if cached is not None:
            self._query_cache.move_to_end(text)
            return cached

        embedding = self._base.embed_query("query: " + text)

        self._query_cache[text] = embedding
        if len(self._query_cache) > _QUERY_CACHE_MAX_SIZE:
            self._query_cache.popitem(last=False)  # evict least-recently-used

        return embedding


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_embedder() -> CachingEmbeddings:
    """Return a cached, caching+batching LangChain-compatible embedder."""
    global _embedder_instance
    if _embedder_instance is not None:
        return _embedder_instance

    embedder_type = settings.LIBRARIAN_EMBEDDER_TYPE
    model_name = settings.LIBRARIAN_EMBEDDING_MODEL

    if embedder_type == "HuggingFace":
        from langchain_huggingface import HuggingFaceEmbeddings
        
        # FIX: The meta-tensor error occurs because device constraints 
        # were incorrectly applied to model_kwargs. 
        # Since CPU usage is already forced via OS environment variables 
        # at the top of the file, we can safely remove model_kwargs. 
        # sentence-transformers will detect the environment variables and 
        # load the Safetensor weights onto the CPU appropriately.
        base = HuggingFaceEmbeddings(
            model_name=model_name,
            encode_kwargs={'normalize_embeddings': True}
        )
    else:
        raise ValueError(
            f"Unknown LIBRARIAN_EMBEDDER_TYPE: '{embedder_type}'. "
            f"Supported: 'HuggingFace'"
        )

    _embedder_instance = CachingEmbeddings(base)
    return _embedder_instance


def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Embed a list of text strings and return their vectors.

    Batching and caching are handled transparently by CachingEmbeddings.
    """
    return get_embedder().embed_documents(texts)


def embed_query(query_text: str) -> list[float]:
    """
    Embed a single query string for similarity search.

    Uses the asymmetric query path of the underlying model, cached (#11).
    """
    return get_embedder().embed_query(query_text)