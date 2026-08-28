"""
Seed the homepage with the layout it already had.

Before this change the homepage was four hardcoded sections in a fixed
order.  Adding `body` and `layout` alone would have blanked it, so this
migration writes those same sections back as blocks, placed on the grid in
the same arrangement.  Deploying it changes nothing a reader can see; the
difference is that an editor can now move any of it.

Placement below matches the old CSS as closely as a 12-column grid can:
`grid-template-columns: 400px minmax(300px, 1fr) 280px` at the ~1421px
content width is roughly 4 / 5 / 3 columns.

The reverse operation clears both fields, so the migration is safely
reversible during development.
"""
import json
import uuid

from django.db import migrations


def _block(block_type, value):
    return {'type': block_type, 'id': str(uuid.uuid4()), 'value': value}


def _source(count, auto_from='latest', exclude_shown=True):
    """A ContentSourceBlock value in its plain automatic mode — what the old
    hardcoded querysets did."""
    return {
        'source_mode': 'auto',
        'picks': [],
        'auto_from': auto_from,
        'topic': None,
        'author': None,
        'volume': None,
        'issue': None,
        'window': '30',
        'count': count,
        'exclude_shown': exclude_shown,
    }


# col / span / row / row_span, mirroring the old fixed layout.
PLACEMENTS = {
    'issue_cover':      (1, 4, 1, 3),
    'issue_carousel':   (5, 5, 1, 3),
    'other_topics':     (10, 3, 1, 3),
    'subscribe_banner': (1, 12, 4, 1),
    'past_issues':      (1, 12, 5, 2),
    'latest_articles':  (1, 12, 7, 2),
}


def _placement(key):
    col, span, row, row_span = PLACEMENTS[key]
    return {
        'col': col, 'span': span, 'row': row, 'row_span': row_span,
        'visible': True, 'style': 'plain', 'pad': 'md',
    }


def _first_pk(apps, app_label, model_name):
    model = apps.get_model(app_label, model_name)
    obj = model.objects.first()
    return obj.pk if obj else None


def _build(apps):
    issue_index = _first_pk(apps, 'issue', 'IssueIndexPage')
    article_index = _first_pk(apps, 'articles', 'ArticleIndexPage')

    blocks = [
        ('issue_cover', _block('issue_cover', {
            'issue_source': 'latest', 'issue': None,
            'label_text': '', 'show_cta': True, 'cta_label': '',
        })),
        ('issue_carousel', _block('issue_carousel', {
            'issue_source': 'latest', 'issue': None,
            'heading': '', 'filter_by': 'same_topic', 'picks': [],
            'autoplay': True, 'pause_ms': 4000, 'show_thumbnails': True,
        })),
        ('other_topics', _block('other_topics', {
            'issue_source': 'latest', 'issue': None,
            'heading': '', 'max_items': 12,
        })),
        ('subscribe_banner', _block('subscribe_banner', {
            'kicker': '', 'heading': '', 'cta_label': '',
            'subscriber_cta_label': '', 'hide_for_subscribers': True,
        })),
        # Four past issues, current one excluded — exclude_shown does that
        # now, because the issue blocks above mark the current issue as shown.
        ('past_issues', _block('past_issues', {
            'heading': 'മുൻപത്തെ ലക്കങ്ങൾ',
            'source': _source(4),
            'layout': 'grid', 'columns': 4,
            'show_more_link': issue_index is not None,
            'more_link_label': '', 'more_link_page': issue_index,
        })),
        ('latest_articles', _block('latest_articles', {
            'heading': 'മറ്റുള്ള ലേഖനങ്ങള്‍',
            'source': _source(6),
            'layout': 'grid', 'columns': 3,
            'show_more_link': article_index is not None,
            'more_link_label': '', 'more_link_page': article_index,
        })),
    ]

    body = [block for _key, block in blocks]
    layout = {
        'version': 1,
        'desktop': {block['id']: _placement(key) for key, block in blocks},
        # Empty: tablet and mobile inherit desktop and reflow, reproducing
        # the old `grid-template-columns: 1fr` collapse in home.css.
        'tablet': {},
        'mobile': {},
    }
    return body, layout


def seed(apps, schema_editor):
    HomePage = apps.get_model('home', 'HomePage')

    # Revision backfill is best-effort: seeding the live page is the part
    # that matters, and a missing model or content type should never be the
    # reason a deploy's migrate step fails.
    try:
        Revision = apps.get_model('wagtailcore', 'Revision')
        ContentType = apps.get_model('contenttypes', 'ContentType')
        homepage_ct = ContentType.objects.filter(
            app_label='home', model='homepage'
        ).first()
    except LookupError:
        Revision, homepage_ct = None, None

    for page in HomePage.objects.all():
        if page.body:
            continue  # already seeded, or an editor got here first

        body, layout = _build(apps)
        page.body = json.dumps(body)
        page.layout = layout
        page.save(update_fields=['body', 'layout'])

        # Patch existing revisions too.  A revision's JSON is a snapshot of
        # the page's fields, and every revision taken before this migration
        # predates `body` and `layout`.  Publishing one of those later would
        # restore a page with no sections at all — a blank homepage — so
        # backfill them with the same seed.
        if Revision is None or homepage_ct is None:
            continue
        revisions = Revision.objects.filter(
            content_type=homepage_ct, object_id=str(page.pk),
        )
        for revision in revisions.iterator():
            content = revision.content
            if not isinstance(content, dict) or content.get('body'):
                continue
            content['body'] = json.dumps(body)
            content['layout'] = layout
            revision.content = content
            revision.save(update_fields=['content'])


def unseed(apps, schema_editor):
    HomePage = apps.get_model('home', 'HomePage')
    HomePage.objects.all().update(body='[]', layout={})


class Migration(migrations.Migration):

    dependencies = [
        ('home', '0013_homepage_body_layout'),
        ('articles', '0006_alter_article_body_alter_article_body_en_and_more'),
        ('issue', '0009_issue_featured_article'),
        # Same wagtailcore pin home/0001_initial already uses — Revision has
        # existed well before it, so the historical model below resolves.
        ('wagtailcore', '0096_referenceindex_referenceindex_source_object_and_more'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
