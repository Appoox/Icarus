-- Runs automatically the FIRST time the db container starts against an
-- empty data directory (Postgres's own docker-entrypoint-initdb.d convention).
-- If you ever need to re-run this against an existing DB, connect with
-- psql and run the CREATE EXTENSION line manually instead.

-- Enables pgvector, so you can use vector columns / similarity search
-- from Django (e.g. via the `pgvector` Python package's VectorField).
CREATE EXTENSION IF NOT EXISTS vector;

-- Note: LISTEN/NOTIFY, which channels_postgres relies on for the Channels
-- layer, is built into Postgres core - there's no extension for it and
-- nothing else needs enabling here.
