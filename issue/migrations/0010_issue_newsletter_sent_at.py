"""
Track whether an issue's newsletter has been sent.

Every issue that was already published before this migration is marked as
sent. The newsletter receiver was never connected before (reader's AppConfig
did not load), so none of these were ever mailed — but mailing them now, the
first time an editor touches an old issue, would be worse.
"""
from django.db import migrations, models


def mark_published_issues_as_sent(apps, schema_editor):
    Issue = apps.get_model("issue", "Issue")
    # first_published_at lives on the parent Page table, and an UPDATE can't
    # reference a joined column — so one row at a time (there are few issues).
    published = Issue.objects.filter(first_published_at__isnull=False)
    for pk, first_published_at in published.values_list("pk", "first_published_at"):
        Issue.objects.filter(pk=pk).update(newsletter_sent_at=first_published_at)


class Migration(migrations.Migration):

    dependencies = [
        ("issue", "0009_issue_featured_article"),
    ]

    operations = [
        migrations.AddField(
            model_name="issue",
            name="newsletter_sent_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.RunPython(mark_published_issues_as_sent, migrations.RunPython.noop),
    ]
