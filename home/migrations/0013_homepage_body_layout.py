import wagtail.fields
from django.db import migrations, models

import home.blocks
import home.layout


class Migration(migrations.Migration):
    """
    Give HomePage the two fields the layout canvas edits.

    The StreamField references home.blocks.HOMEPAGE_BLOCKS rather than
    inlining the block definitions.  Block definitions carry no schema —
    the column is JSON either way — so inlining a copy here would buy
    nothing and guarantee it drifts out of step with blocks.py.
    """

    dependencies = [
        ('home', '0012_siteheader_dark_logos'),
    ]

    operations = [
        migrations.AddField(
            model_name='homepage',
            name='body',
            field=wagtail.fields.StreamField(
                home.blocks.HOMEPAGE_BLOCKS,
                blank=True,
                help_text=(
                    'The sections on the homepage. Arrange them on the grid '
                    'from Homepage Layout in the sidebar.'
                ),
                verbose_name='Sections',
            ),
        ),
        migrations.AddField(
            model_name='homepage',
            name='layout',
            field=models.JSONField(
                blank=True,
                default=home.layout.empty_layout,
                help_text=(
                    'Grid placement per section, keyed by block ID. Edited by '
                    'the Homepage Layout canvas — not meant to be hand-edited.'
                ),
            ),
        ),
    ]
