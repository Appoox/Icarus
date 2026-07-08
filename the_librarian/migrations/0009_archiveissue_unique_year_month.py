from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('the_librarian', '0008_add_embedding_hnsw_index'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='archiveissue',
            constraint=models.UniqueConstraint(
                fields=['year', 'month'],
                name='unique_archive_issue_year_month',
                violation_error_message=(
                    "An Archive Issue for this year and month already exists. "
                    "Each year–month combination can only be uploaded once — "
                    "edit the existing entry instead of creating a duplicate "
                    "under a different filename."
                ),
            ),
        ),
    ]