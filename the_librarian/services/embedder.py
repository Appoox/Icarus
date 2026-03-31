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

"""

import os
# Force CPU usage and disable GPU detection to prevent meta-tensor loading issues (#77)
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ["ACCELERATE_USE_CPU"] = "true"

from django.conf import settings

batch_size = settings.LIBRARIAN_EMBED_BATCH_SIZE
_embedder_instance = None


# ---------------------------------------------------------------------------
# #3 + #10: Caching + batching wrapper
# ---------------------------------------------------------------------------

class CachingEmbeddings:
    """
    LangChain-compatible embeddings wrapper that:

    - Caches embed_documents results in an in-memory dict so identical texts
      are never sent to the model twice within the same process lifetime.
    - Processes texts in batches of EMBED_BATCH_SIZE to avoid OOM errors on
      large documents (#10).

    The cache is intentionally unbounded and process-scoped.  For very large
    archives this trades memory for speed; adjust EMBED_BATCH_SIZE or add an
    LRU eviction policy if memory becomes a concern.
    """

    def __init__(self, base_embedder):
        self._base = base_embedder
        self._cache: dict[str, list[float]] = {}

    # LangChain's SemanticChunker calls embed_documents internally.
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        uncached = [t for t in texts if t not in self._cache]

        if uncached:
            new_embeddings: list[list[float]] = []
            # #10: iterate in fixed-size batches
            for i in range(0, len(uncached), batch_size):
                batch = uncached[i : i + batch_size]
                new_embeddings.extend(self._base.embed_documents(batch))
            for text, emb in zip(uncached, new_embeddings):
                self._cache[text] = emb

        return [self._cache[t] for t in texts]

    # embed_query uses a separate asymmetric path — no batching needed.
    def embed_query(self, text: str) -> list[float]:
        return self._base.embed_query(text)


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
        base = HuggingFaceEmbeddings(
            model_name=model_name,
            model_kwargs={
                'device': 'cpu'
            },
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

    Uses the asymmetric query path of the underlying model.
    """
    return get_embedder().embed_query(query_text)