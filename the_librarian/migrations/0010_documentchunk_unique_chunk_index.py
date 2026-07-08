from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('the_librarian', '0009_archiveissue_unique_year_month'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='documentchunk',
            constraint=models.UniqueConstraint(
                condition=models.Q(('document__isnull', False)),
                fields=('document', 'chunk_index'),
                name='unique_document_chunk_index',
            ),
        ),
    ]