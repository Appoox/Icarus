"""
Split the free-article meter from the reading history.

read_articles was both the opt-in reading history and the paywall meter, so
withdrawing consent (which clears history) reset the meter: tick and untick
the box and the free articles started over. metered_articles is the meter
now. Everything a reader has opened so far is copied into it, so nobody
gains free reads from this change.
"""
from django.db import migrations, models


def copy_reads_into_meter(apps, schema_editor):
    ReaderUser = apps.get_model("reader", "ReaderUser")
    History = ReaderUser.read_articles.through
    Meter = ReaderUser.metered_articles.through

    batch = []
    for row in History.objects.values("readeruser_id", "article_id").iterator(chunk_size=2000):
        batch.append(Meter(readeruser_id=row["readeruser_id"], article_id=row["article_id"]))
        if len(batch) >= 2000:
            Meter.objects.bulk_create(batch, ignore_conflicts=True)
            batch = []
    if batch:
        Meter.objects.bulk_create(batch, ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        ("articles", "0006_alter_article_body_alter_article_body_en_and_more"),
        ("reader", "0012_alter_paymentdetails_options_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="readeruser",
            name="metered_articles",
            field=models.ManyToManyField(
                blank=True,
                help_text="സൗജന്യ ലേഖന പരിധിയിൽ എണ്ണിയ ലേഖനങ്ങൾ.",
                related_name="metered_by",
                to="articles.article",
            ),
        ),
        migrations.RunPython(copy_reads_into_meter, migrations.RunPython.noop),
    ]
