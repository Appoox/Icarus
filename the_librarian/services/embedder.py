"""
Swappable embedder service for The Librarian.

Reads LIBRARIAN_EMBEDDER_TYPE and LIBRARIAN_EMBEDDING_MODEL from Django settings.
The embedder is cached as a module-level singleton to avoid reloading the model
on every call.

To swap models, change EMBEDDING_MODEL_NAME and EMBEDDING_DIM in your .env file.
"""
from django.conf import settings

_embedder_instance = None


def get_embedder():
    """Return a cached LangChain-compatible embedder instance."""
    global _embedder_instance
    if _embedder_instance is not None:
        return _embedder_instance

    embedder_type = settings.LIBRARIAN_EMBEDDER_TYPE
    model_name = settings.LIBRARIAN_EMBEDDING_MODEL

    if embedder_type == "HuggingFace":
        from langchain_huggingface import HuggingFaceEmbeddings
        _embedder_instance = HuggingFaceEmbeddings(model_name=model_name)
    else:
        raise ValueError(
            f"Unknown LIBRARIAN_EMBEDDER_TYPE: '{embedder_type}'. "
            f"Supported: 'HuggingFace'"
        )

    return _embedder_instance


def embed_texts(texts):
    """
    Embed a list of text strings and return their vectors.

    Args:
        texts: list[str] — the texts to embed.

    Returns:
        list[list[float]] — one embedding vector per input text.
    """
    embedder = get_embedder()
    return embedder.embed_documents(texts)


def embed_query(query_text):
    """
    Embed a single query string for similarity search.

    Args:
        query_text: str — the search query.

    Returns:
        list[float] — the embedding vector.
    """
    embedder = get_embedder()
    return embedder.embed_query(query_text)
