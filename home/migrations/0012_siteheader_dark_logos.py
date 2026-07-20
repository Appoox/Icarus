import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("wagtailimages", "0027_image_description"),
        ("home", "0011_alter_footercolumnlink_dynamic_url_key"),
    ]

    operations = [
        migrations.AddField(
            model_name="siteheader",
            name="logo_dark",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional logo variant for the dark themes; the regular logo is used when empty.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="wagtailimages.image",
            ),
        ),
        migrations.AddField(
            model_name="siteheader",
            name="site_title_image_dark",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional site-title image variant for the dark themes; the regular image is used when empty.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="wagtailimages.image",
            ),
        ),
    ]
