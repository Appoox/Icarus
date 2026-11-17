"""
How text is labelled before it is turned into a vector.

Shared by the site (services/embedder.py) and the remote worker
(worker/ingest_worker.py) so both produce the same vector for the same
chunk. Kept free of Django imports on purpose: the worker imports it
without Django installed.

The labels are the convention of the e5 model family (the site runs
intfloat/multilingual-e5-large): stored text starts with "passage: " and
search text with "query: ". Change them here, for both sides at once, if
the site moves to a model with a different convention — and re-embed the
stored chunks afterwards, since old and new vectors won't match.
"""

DOCUMENT_PREFIX = "passage: "
QUERY_PREFIX = "query: "
