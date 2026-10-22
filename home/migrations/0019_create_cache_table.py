"""
Create the database cache table (settings.CACHES: DatabaseCache "icarus_cache").

The cache table isn't a model, so `migrate` never made it: a database that had
been migrated but not given `createcachetable` failed on every page with
'relation "icarus_cache" does not exist'. This runs createcachetable as part of
migrate. It only ever adds the table, does nothing if it already exists, and
touches no data.
"""
from django.core.management import call_command
from django.db import migrations


def create_cache_table(apps, schema_editor):
    call_command("createcachetable", database=schema_editor.connection.alias, verbosity=0)


class Migration(migrations.Migration):
    dependencies = [
        ("home", "0018_scrub_personal_data_from_audit_log"),
    ]

    operations = [
        migrations.RunPython(create_cache_table, migrations.RunPython.noop),
    ]
