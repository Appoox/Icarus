"""
the_librarian/services/vector_schema.py
────────────────────────────────────────
Defines the wire contract for one embedded chunk between the remote
ingestion worker and this server — independent of whatever library is
actually storing the vector today (pgvector.VectorField).

Deliberately has NO Django imports. If the storage backend changes later,
this is the one file whose validation logic might need to change; the
worker-server *contract* doesn't move. expected_dim is passed in
explicitly by the caller (e.g. from settings.LIBRARIAN_EMBEDDING_DIM) —
never read from Django settings directly — so this module stays usable
even from a plain script with no Django installed at all.
"""
from dataclasses import dataclass

MAX_CHUNK_TEXT_LENGTH = 5000
MAX_CHUNKS_PER_BATCH = 200   # keeps every request body small regardless
                              # of how many chunks any one document turns
                              # out to have — see services/ingestion.py


class VectorValidationError(ValueError):
    """Raised when a chunk payload doesn't match the agreed wire contract."""


@dataclass(frozen=True)
class VectorRecord:
    chunk_text: str
    page_number: int | None
    chunk_index: int
    embedding: list[float]


def validate_chunk_payload(raw: dict, *, expected_dim: int) -> VectorRecord:
    if not isinstance(raw, dict):
        raise VectorValidationError("Each chunk must be a JSON object.")

    chunk_text = raw.get("chunk_text")
    if not isinstance(chunk_text, str) or not chunk_text.strip():
        raise VectorValidationError("chunk_text must be a non-empty string.")
    if len(chunk_text) > MAX_CHUNK_TEXT_LENGTH:
        raise VectorValidationError(
            f"chunk_text exceeds {MAX_CHUNK_TEXT_LENGTH} characters — "
            f"the worker's chunker should have split this further."
        )

    chunk_index = raw.get("chunk_index")
    if not isinstance(chunk_index, int) or isinstance(chunk_index, bool) or chunk_index < 0:
        raise VectorValidationError("chunk_index must be a non-negative integer.")

    page_number = raw.get("page_number")
    if page_number is not None and (
        not isinstance(page_number, int) or isinstance(page_number, bool) or page_number < 1
    ):
        raise VectorValidationError("page_number must be a positive integer or null.")

    embedding = raw.get("embedding")
    if not isinstance(embedding, list) or not embedding:
        raise VectorValidationError("embedding must be a non-empty list of numbers.")
    if len(embedding) != expected_dim:
        raise VectorValidationError(
            f"embedding has {len(embedding)} dimensions, expected {expected_dim}. "
            f"Check the worker is using the same embedding model as this server "
            f"(LIBRARIAN_EMBEDDING_MODEL / LIBRARIAN_EMBEDDING_DIM)."
        )
    for x in embedding:
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            raise VectorValidationError("embedding must contain only numbers.")
        if x != x or x in (float("inf"), float("-inf")):  # NaN / Inf
            raise VectorValidationError(
                "embedding contains NaN or infinite values — these would "
                "silently corrupt cosine-distance search results."
            )

    return VectorRecord(
        chunk_text=chunk_text.strip(),
        page_number=page_number,
        chunk_index=chunk_index,
        embedding=embedding,
    )


def validate_batch(chunks, *, expected_dim: int) -> list[VectorRecord]:
    if not isinstance(chunks, list):
        raise VectorValidationError("'chunks' must be a list.")
    if not chunks:
        raise VectorValidationError("'chunks' must not be empty.")
    if len(chunks) > MAX_CHUNKS_PER_BATCH:
        raise VectorValidationError(
            f"Batch contains {len(chunks)} chunks — split into batches of "
            f"{MAX_CHUNKS_PER_BATCH} or fewer."
        )
    return [validate_chunk_payload(c, expected_dim=expected_dim) for c in chunks]