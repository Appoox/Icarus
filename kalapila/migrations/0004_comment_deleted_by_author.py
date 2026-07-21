from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('kalapila', '0003_comment_issue'),
    ]

    operations = [
        migrations.AddField(
            model_name='comment',
            name='deleted_by_author',
            field=models.BooleanField(default=False),
        ),
    ]
