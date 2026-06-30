from django.conf import settings
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Postbox',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('feedback_type', models.CharField(
                    choices=[('bug', 'Bug Report'), ('suggestion', 'Suggestion'),
                             ('content', 'Content Issue'), ('general', 'General Feedback')],
                    default='general', max_length=20, verbose_name='Type',
                )),
                ('feedback', models.TextField(verbose_name='Feedback')),
                ('rating', models.PositiveSmallIntegerField(
                    blank=True, null=True,
                    choices=[(1, 1), (2, 2), (3, 3), (4, 4), (5, 5)],
                    verbose_name='Rating',
                )),
                ('page_context', models.CharField(blank=True, max_length=500, verbose_name='Page context')),
                ('submitted_at', models.DateTimeField(auto_now_add=True)),
                ('is_reviewed', models.BooleanField(default=False, verbose_name='Reviewed')),
                ('admin_notes', models.TextField(blank=True, verbose_name='Admin notes')),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='feedback_submissions',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Postbox',
                'verbose_name_plural': 'Postbox Submissions',
                'ordering': ['-submitted_at'],
            },
        ),
    ]