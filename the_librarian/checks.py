"""
System check: the configured embedding size must match the vector column.

DocumentChunk.embedding takes its dimension from settings
(EMBEDDING_DIM, default 768), but the column was created by migration
0001 as vector(1024). If the two differ, every chunk insert fails
("expected 1024 dimensions, not 768") and similarity search breaks.

The comparison uses the migration history (no database connection), and
it is a Warning, not an Error, so a mismatch is reported loudly at
`manage.py migrate`/`check` without stopping the container from booting.
"""
from django.conf import settings
from django.core.checks import Warning, register


def _migrated_dimension():
    from django.db.migrations.loader import MigrationLoader

    state = MigrationLoader(None, ignore_no_migrations=True).project_state()
    model = state.models.get(("the_librarian", "documentchunk"))
    field = model.fields.get("embedding") if model else None
    return getattr(field, "dimensions", None)


@register()
def embedding_dimension_matches_schema(app_configs, **kwargs):
    configured = getattr(settings, "LIBRARIAN_EMBEDDING_DIM", None)
    try:
        migrated = _migrated_dimension()
    except Exception:  # never let the check itself break startup
        return []
    if migrated is None or configured == migrated:
        return []
    return [
        Warning(
            f"EMBEDDING_DIM is {configured}, but the_librarian's vector column "
            f"is vector({migrated}) in the migrations.",
            hint=(
                f"Set EMBEDDING_DIM={migrated} (and an embedding model that "
                f"produces {migrated}-dimension vectors), or add a migration "
                f"that changes the column and re-embed the archive."
            ),
            id="the_librarian.W001",
        )
    ]
