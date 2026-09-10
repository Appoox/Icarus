from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('articles', '0006_alter_article_body_alter_article_body_en_and_more'),
        ('issue', '0008_alter_issue_editorial'),
    ]

    operations = [
        migrations.AddField(
            model_name='issue',
            name='featured_article',
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "The lead article for this issue. The homepage's Featured "
                    "Article section can be set to follow this, so setting it "
                    "here is enough — the homepage updates itself when the "
                    "next issue publishes."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+',
                to='articles.article',
            ),
        ),
    ]
