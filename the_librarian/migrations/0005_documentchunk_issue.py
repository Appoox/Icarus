# Generated migration — add `issue` FK to DocumentChunk for editorial indexing.
# Apply after: issue/0007_alter_issue_editorial

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('issue', '0007_alter_issue_editorial'),
        ('the_librarian', '0004_alter_archiveissue_thumbnail_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='documentchunk',
            name='issue',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='chunks',
                to='issue.issue',
            ),
        ),
    ]
