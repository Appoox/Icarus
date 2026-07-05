# Generated manually — adds an HNSW approximate-nearest-neighbor index on
# DocumentChunk.embedding. Without this, every CosineDistance query in
# the_librarian/services/search.py does an exact sequential scan over all
# rows, which is why search feels slow and gets worse as the archive grows.
# Requires the pgvector Postgres EXTENSION to be >= 0.5.0 (HNSW support was

from pgvector.django import HnswIndex
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('the_librarian', '0007_documentchunk_topic'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='documentchunk',
            index=HnswIndex(
                name='chunk_embedding_hnsw',
                fields=['embedding'],
                m=16,
                ef_construction=64,
                opclasses=['vector_cosine_ops'],
            ),
        ),
    ]