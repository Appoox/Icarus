"""Enable the pgvector extension in PostgreSQL.
This must run before any migration that uses VectorField.
"""
from django.db import migrations
from pgvector.django import VectorExtension


class Migration(migrations.Migration):

    dependencies = []

    operations = [
        VectorExtension(),
    ]
