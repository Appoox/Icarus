import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('the_librarian', '0010_documentchunk_unique_chunk_index'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='LibrarianAPIKey',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text="A label to identify this key, e.g. 'Home workstation' or 'Laptop worker'.", max_length=100)),
                ('key_hash', models.CharField(editable=False, help_text='SHA-256 hash of the API key. The plaintext key is shown once at creation and never stored.', max_length=64, unique=True)),
                ('key_prefix', models.CharField(editable=False, help_text='First few characters of the key, shown in the admin so keys can be told apart without exposing the full value.', max_length=16)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('last_used_at', models.DateTimeField(blank=True, null=True)),
                ('is_active', models.BooleanField(default=True)),
                ('revoked_at', models.DateTimeField(blank=True, null=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('revoked_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Librarian API Key',
                'verbose_name_plural': 'Librarian API Keys',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='LibrarianRemoteIngestSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('enabled', models.BooleanField(default=False)),
                ('enabled_at', models.DateTimeField(blank=True, null=True)),
                ('pending_total_at_enable', models.PositiveIntegerField(default=0, help_text='Snapshot of un-ingested ArchiveIssues at the moment this was last enabled — the denominator for the dashboard progress bar.')),
                ('enabled_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Remote Ingestion Worker Settings',
            },
        ),
        migrations.CreateModel(
            name='LibrarianWorkerAccessLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('remote_addr', models.GenericIPAddressField(blank=True, null=True)),
                ('event', models.CharField(choices=[('auth_failed', 'Authentication Failed'), ('disabled', 'Rejected — Remote Ingestion Disabled'), ('pending_check', 'Listed Pending Issues'), ('download', 'Downloaded PDF'), ('submit_ok', 'Submitted Chunk Batch'), ('submit_rejected', 'Chunk Batch Rejected')], max_length=20)),
                ('detail', models.CharField(blank=True, max_length=255)),
                ('chunk_count', models.PositiveIntegerField(blank=True, null=True)),
                ('api_key', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='access_logs', to='the_librarian.librarianapikey')),
                ('archive_issue', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='worker_log_entries', to='the_librarian.archiveissue')),
            ],
            options={
                'verbose_name': 'Librarian Worker Access Log',
                'verbose_name_plural': 'Librarian Worker Access Logs',
                'ordering': ['-created_at'],
            },
        ),
    ]