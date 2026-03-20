"""
Migration: add HNSW index on DocumentChunk.embedding for fast ANN search.

Improvement #9
--------------
Without an index, every pgvector cosine search is a full sequential scan
over all DocumentChunk rows.  This degrades rapidly beyond ~100 k chunks.

An HNSW (Hierarchical Navigable Small World) index turns exact nearest-
neighbour search into approximate nearest-neighbour (ANN) search.  It trades
a tiny, tunable amount of recall for O(log N) query time instead of O(N).

How to apply
------------
1. Verify that this file's `dependencies` tuple points at the migration that
   creates the `documentchunk` table (adjust the name if yours differs from
   '0001_initial').
2. Run:  python manage.py migrate the_librarian

HNSW build parameters (can be tuned):
  m              = 16  — max edges per node; higher → better recall, more RAM
  ef_construction = 64  — search width during build; higher → better index quality

Query-time ef (controls recall vs speed at search time) can be set per-session:
  SET hnsw.ef_search = 100;   -- default is 40
"""
from django.db import migrations


class Migration(migrations.Migration):

    # ------------------------------------------------------------------ #
    # Adjust the dependency name to match your actual initial migration.  #
    # ------------------------------------------------------------------ #
    dependencies = [
        ("the_librarian", "0002_initial"),
    ]

    operations = [
        # Use RunSQL so we can supply both the forward and reverse SQL.
        # The index is created CONCURRENTLY so it does not lock the table
        # during the build — safe to run in production.
        #
        # NOTE: CREATE INDEX CONCURRENTLY cannot run inside a transaction.
        # Django wraps each migration in a transaction by default, so we
        # must set atomic = False on this migration.
        migrations.RunSQL(
            sql="""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS
                    documentchunk_embedding_hnsw_idx
                ON the_librarian_documentchunk
                USING hnsw (embedding vector_cosine_ops)
                WITH (m = 16, ef_construction = 64);
            """,
            reverse_sql="""
                DROP INDEX CONCURRENTLY IF EXISTS
                    documentchunk_embedding_hnsw_idx;
            """,
        ),
    ]

    # Required because CREATE INDEX CONCURRENTLY cannot run inside a
    # transaction block.
    atomic = False
