"""
home/blocks.py
─────────────────────────────────────────────────────────────────────────
The homepage section catalogue.

Each block here is one placeable section on the homepage grid.  Blocks are
declarative: they describe their editable fields and hand data resolution
off to home/curation.py, so adding a new section type means adding a
StructBlock and a template, not touching the renderer.

Three families:

  Curated   featured_article, article_group, issue_group, author_spotlight,
            topic_row — anything where an editor may hand-pick items.
  Issue     issue_cover, issue_carousel, other_topics — the three parts the
            old hardcoded "featured issue" section used to weld together.
            Each now carries its own issue_source, so they are independent
            and can be placed anywhere, at any size.
  Free-form rich_text, custom_card, card_group, image_banner, cta_button,
            embed, quote, raw_html, divider, spacer, subscribe_banner —
            for building elements that don't exist in the content models.

── Adding a section type ────────────────────────────────────────────────
  1. Define the StructBlock here, with an `icon` and a `label`.
  2. Give it a template in home/templates/blocks/<name>_block.html.
  3. Add it to HOMEPAGE_BLOCKS below, in the palette group you want it to
     appear under in the canvas.
Nothing else needs to change — the canvas reads its palette from
HOMEPAGE_BLOCKS, and the grid renderer just calls {% include_block %}.
"""
from django.utils.translation import gettext_lazy as _

from wagtail import blocks
from wagtail.embeds.blocks import EmbedBlock
from wagtail.images.blocks import ImageChooserBlock
from wagtail.snippets.blocks import SnippetChooserBlock

from articles.wagtail_widgets import ColorPickerBlock

from home import curation


# ── Shared vocabularies ─────────────────────────────────────────────────

SIZE_CHOICES = [
    ('hero',      _('Hero — full width, large image')),
    ('large',     _('Large')),
    ('medium',    _('Medium')),
    ('compact',   _('Compact — small thumbnail')),
    ('text_only', _('Text only — no image')),
]

LAYOUT_CHOICES = [
    ('grid',     _('Grid')),
    ('row',      _('Row — horizontal scroll')),
    ('list',     _('List — stacked, thumbnail left')),
    ('carousel', _('Carousel — auto-advancing')),
    ('masonry',  _('Masonry')),
]

SOURCE_MODE_CHOICES = [
    ('auto',             _('Automatic — always show the newest / most read')),
    ('manual',           _('Hand-picked — show exactly what I choose')),
    ('pinned_then_auto', _('Hand-picked, then top up automatically')),
]

AUTO_FROM_CHOICES = [
    ('latest',        _('Latest published')),
    ('most_read',     _('Most read')),
    ('current_issue', _('Articles in the current issue')),
    ('by_topic',      _('From a topic')),
    ('by_author',     _('By an author')),
    ('by_volume',     _('From a volume')),
    ('by_issue',      _('From a specific issue')),
]

WINDOW_CHOICES = [
    ('7',  _('Last 7 days')),
    ('30', _('Last 30 days')),
    ('90', _('Last 90 days')),
    ('0',  _('All time')),
]

ISSUE_SOURCE_CHOICES = [
    ('latest', _('The current issue — updates when a new issue publishes')),
    ('pick',   _('A specific issue')),
]


# ── Curation primitives ─────────────────────────────────────────────────

class CuratedItemBlock(blocks.StructBlock):
    """
    One hand-picked item, plus how it should look *here*.

    Every override is optional and blank means "use the page's own value",
    so pinning something costs a single click.  The overrides exist so a
    piece can lead the homepage under a punchier headline, or with a crop
    that works at hero size, without altering the article itself.
    """

    # Deliberately not required.  The canvas is a working surface: an editor
    # adds a row, then goes looking for the article to put in it.  If an
    # unfilled row failed validation, a Save draft mid-edit would be rejected
    # for the whole page — every other section with it.  An empty pick is
    # simply skipped at render time (see cards_from_picks in curation.py).
    item = blocks.PageChooserBlock(
        page_type=['articles.Article', 'issue.Issue', 'literati.Literati'],
        required=False,
        label=_('Item'),
    )
    size = blocks.ChoiceBlock(
        choices=SIZE_CHOICES, default='medium', required=False,
        label=_('Size'),
        help_text=_('How prominent this item is within the section.'),
    )
    kicker = blocks.CharBlock(
        required=False, max_length=40, label=_('Kicker'),
        help_text=_('Small label above the headline — e.g. EDITOR’S PICK, INTERVIEW.'),
    )
    headline_override = blocks.CharBlock(
        required=False, max_length=255, label=_('Headline override'),
        help_text=_('Leave blank to use the item’s own title.'),
    )
    standfirst = blocks.TextBlock(
        required=False, label=_('Standfirst'),
        help_text=_('A short summary shown only here.'),
    )
    image_override = ImageChooserBlock(
        required=False, label=_('Image override'),
        help_text=_('Leave blank to use the item’s own cover image.'),
    )
    badge = blocks.CharBlock(
        required=False, max_length=24, label=_('Badge'),
        help_text=_('Small corner flag — e.g. NEW, FREE TO READ.'),
    )
    accent_colour = ColorPickerBlock(
        required=False, label=_('Accent colour'),
        help_text=_('Defaults to the item’s topic colour.'),
    )

    class Meta:
        icon = 'pick'
        label = _('Picked item')


class ContentSourceBlock(blocks.StructBlock):
    """
    Where a section's items come from.

    Composed into every feed block so curation behaves identically
    everywhere.  `pinned_then_auto` is the mode most sections want in
    practice: pin the one or two things that matter this week, let the rest
    fill itself in so the page never goes stale when nobody touches it.

    Only the fields relevant to the chosen mode are meaningful; the canvas
    inspector shows and hides them accordingly, and the resolver in
    curation.py ignores the rest.
    """

    source_mode = blocks.ChoiceBlock(
        choices=SOURCE_MODE_CHOICES, default='auto', required=False,
        label=_('Source'),
    )
    picks = blocks.ListBlock(
        CuratedItemBlock(), required=False, label=_('Hand-picked items'),
        help_text=_('Drag to reorder. Used by the two hand-picked modes.'),
    )
    auto_from = blocks.ChoiceBlock(
        choices=AUTO_FROM_CHOICES, default='latest', required=False,
        label=_('Fill automatically from'),
    )
    topic = SnippetChooserBlock('issue.Topic', required=False, label=_('Topic'))
    author = blocks.PageChooserBlock(
        page_type='literati.Literati', required=False, label=_('Author'),
    )
    volume = SnippetChooserBlock('issue.Volume', required=False, label=_('Volume'))
    issue = blocks.PageChooserBlock(
        page_type='issue.Issue', required=False, label=_('Issue'),
    )
    window = blocks.ChoiceBlock(
        choices=WINDOW_CHOICES, default='30', required=False,
        label=_('Most-read window'),
    )
    count = blocks.IntegerBlock(
        default=6, min_value=1, max_value=48, required=False, label=_('How many to show'),
    )
    exclude_shown = blocks.BooleanBlock(
        required=False, default=True, label=_('Skip items already on the page'),
        help_text=_('Stops the same article appearing twice in two sections. '
                    'Hand-picked items are always shown regardless.'),
    )

    class Meta:
        icon = 'list-ul'
        label = _('Content source')


class SectionBlock(blocks.StructBlock):
    """
    Base for every placeable section.

    `get_context` builds the RenderContext handshake that lets sections
    dedupe against each other — see the module docstring in curation.py for
    why resolution order matters.
    """

    def render_context(self, parent_context):
        """Fetch the shared RenderContext the page renderer put in context."""
        ctx = (parent_context or {}).get('sg_render_ctx')
        if ctx is None:
            # Rendering outside HomePage (a preview of a single block, a test).
            request = (parent_context or {}).get('request')
            ctx = curation.RenderContext(request)
        return ctx


# ── Curated sections ────────────────────────────────────────────────────

class FeaturedArticleBlock(SectionBlock):
    """
    One article, given the full-width treatment.

    The `issue_featured` source is the interesting one: it follows
    Issue.featured_article, so an editor sets the lead piece once on the
    issue itself and the homepage updates automatically when the next issue
    publishes — nobody has to remember to come back here.
    """

    source = blocks.ChoiceBlock(
        choices=[
            ('pick',           _('Choose an article')),
            ('issue_featured', _('The current issue’s featured article')),
            ('most_read',      _('Most read this week')),
        ],
        default='pick', required=False, label=_('Which article'),
    )
    pick = CuratedItemBlock(required=False, label=_('Article'))
    display = blocks.ChoiceBlock(
        choices=[
            ('hero',      _('Hero — image behind, text over')),
            ('split',     _('Split — image beside text')),
            ('poster',    _('Poster — tall image above text')),
            ('text_only', _('Text only')),
        ],
        default='hero', required=False, label=_('Display'),
    )
    show_standfirst = blocks.BooleanBlock(required=False, default=True,
                                          label=_('Show standfirst'))

    class Meta:
        icon = 'star'
        label = _('Featured article')
        template = 'blocks/featured_article_block.html'
        group = _('Curated')

    def get_context(self, value, parent_context=None):
        context = super().get_context(value, parent_context)
        ctx = self.render_context(parent_context)
        card = None

        source = value.get('source')
        if source == 'pick':
            picked = curation.cards_from_picks(
                [value['pick']] if value.get('pick') else [], ctx,
                default_size='hero',
            )
            card = picked[0] if picked else None

        elif source == 'issue_featured':
            issue = ctx.current_issue()
            article = getattr(issue, 'featured_article', None) if issue else None
            if article is not None and article.live:
                # Re-fetch through the prefetched queryset so the byline and
                # topic on the card don't cost extra queries.
                article = curation.article_queryset().filter(pk=article.pk).first()
            if article is not None:
                card = curation.HomeCard(article, size='hero')

        elif source == 'most_read':
            ids = curation.most_read_ids('articles.Article', 7, 1)
            pages = curation.in_id_order(curation.article_queryset(), ids)
            if pages:
                card = curation.HomeCard(pages[0], size='hero')

        if card is not None:
            ctx.mark_seen([card])

        context['card'] = card
        context['sg_render_ctx'] = ctx
        return context


class ItemGroupBlock(SectionBlock):
    """Shared body for the article / issue / author group sections."""

    kind = 'article'
    default_size = 'medium'

    heading = blocks.CharBlock(required=False, max_length=120, label=_('Heading'))
    source = ContentSourceBlock()
    layout = blocks.ChoiceBlock(choices=LAYOUT_CHOICES, default='grid',
                               required=False, label=_('Layout'))
    columns = blocks.IntegerBlock(default=3, min_value=1, max_value=6, required=False,
                                  label=_('Columns'),
                                  help_text=_('Ignored for list and row layouts.'))
    show_more_link = blocks.BooleanBlock(required=False, default=False,
                                         label=_('Show a “see more” link'))
    more_link_label = blocks.CharBlock(required=False, max_length=60,
                                       label=_('“See more” label'))
    more_link_page = blocks.PageChooserBlock(required=False,
                                             label=_('“See more” destination'))

    def get_context(self, value, parent_context=None):
        context = super().get_context(value, parent_context)
        ctx = self.render_context(parent_context)
        cards = curation.resolve(
            value.get('source'), ctx,
            kind=self.kind, default_size=self.default_size,
        )
        ctx.mark_seen(cards)
        context['cards'] = cards
        context['sg_render_ctx'] = ctx
        return context


class ArticleGroupBlock(ItemGroupBlock):
    kind = 'article'

    class Meta:
        icon = 'doc-full'
        label = _('Article group')
        template = 'blocks/article_group_block.html'
        group = _('Curated')


class IssueGroupBlock(ItemGroupBlock):
    kind = 'issue'

    class Meta:
        icon = 'copy'
        label = _('Issue group')
        template = 'blocks/issue_group_block.html'
        group = _('Curated')


class AuthorSpotlightBlock(ItemGroupBlock):
    kind = 'author'
    default_size = 'compact'

    class Meta:
        icon = 'group'
        label = _('Author spotlight')
        template = 'blocks/author_spotlight_block.html'
        group = _('Curated')


class TopicRowBlock(SectionBlock):
    """A topic's name and colour, with its latest articles beneath."""

    topic = SnippetChooserBlock('issue.Topic', required=False, label=_('Topic'))
    count = blocks.IntegerBlock(default=4, min_value=1, max_value=12, required=False,
                                label=_('How many articles'))
    layout = blocks.ChoiceBlock(choices=LAYOUT_CHOICES, default='row',
                                required=False, label=_('Layout'))
    show_topic_link = blocks.BooleanBlock(required=False, default=True,
                                          label=_('Link to the topic page'))

    class Meta:
        icon = 'tag'
        label = _('Topic row')
        template = 'blocks/topic_row_block.html'
        group = _('Curated')

    def get_context(self, value, parent_context=None):
        context = super().get_context(value, parent_context)
        ctx = self.render_context(parent_context)
        topic = value.get('topic')
        cards = []
        if topic is not None:
            qs = (curation.article_queryset()
                  .filter(topic=topic)
                  .exclude(pk__in=ctx.seen_pages)
                  .order_by('-first_published_at')[:value.get('count') or 4])
            cards = [curation.HomeCard(a) for a in qs]
            ctx.mark_seen(cards)
        context['cards'] = cards
        context['sg_render_ctx'] = ctx
        return context


# ── Issue sections ──────────────────────────────────────────────────────

class IssueSourceMixin(blocks.StructBlock):
    """Every issue block resolves its own issue, so the three parts of the
    old welded-together featured-issue section are now independent."""

    issue_source = blocks.ChoiceBlock(choices=ISSUE_SOURCE_CHOICES,
                                      default='latest', required=False,
                                      label=_('Issue'))
    issue = blocks.PageChooserBlock(page_type='issue.Issue', required=False,
                                    label=_('Which issue'))

    def resolve_issue(self, value, ctx):
        if value.get('issue_source') == 'pick':
            issue = value.get('issue')
            issue = issue.specific if issue is not None else None
            if issue is None or not issue.live:
                return None
        else:
            issue = ctx.current_issue()
        # Whichever issue this section is built around counts as shown, so an
        # issue feed placed below it skips the issue instead of repeating it —
        # what the old past_issues.exclude(id=current_issue.id) did by hand.
        ctx.mark_page_seen(issue)
        return issue


class IssueCoverBlock(IssueSourceMixin, SectionBlock):
    label_text = blocks.CharBlock(required=False, max_length=60,
                                  label=_('Label above the cover'))
    show_cta = blocks.BooleanBlock(required=False, default=True,
                                   label=_('Show the call-to-action button'))
    cta_label = blocks.CharBlock(required=False, max_length=60,
                                 label=_('Call-to-action label'))

    class Meta:
        icon = 'image'
        label = _('Issue cover')
        template = 'blocks/issue_cover_block.html'
        group = _('Issue')

    def get_context(self, value, parent_context=None):
        context = super().get_context(value, parent_context)
        ctx = self.render_context(parent_context)
        context['issue'] = self.resolve_issue(value, ctx)
        context['sg_render_ctx'] = ctx
        return context


class IssueCarouselBlock(IssueSourceMixin, SectionBlock):
    heading = blocks.CharBlock(required=False, max_length=120, label=_('Heading'))
    filter_by = blocks.ChoiceBlock(
        choices=[
            ('same_topic',   _('Articles on the issue’s own topic')),
            ('other_topics', _('Articles on other topics')),
            ('all',          _('All articles in the issue')),
            ('curated',      _('Hand-picked')),
        ],
        default='same_topic', required=False, label=_('Which articles'),
    )
    picks = blocks.ListBlock(CuratedItemBlock(), required=False,
                             label=_('Hand-picked articles'),
                             help_text=_('Used when “Hand-picked” is selected above.'))
    autoplay = blocks.BooleanBlock(required=False, default=True,
                                   label=_('Advance automatically'))
    pause_ms = blocks.IntegerBlock(default=4000, min_value=1000, max_value=30000,
                                   required=False,
                                   label=_('Pause between slides (ms)'))
    show_thumbnails = blocks.BooleanBlock(required=False, default=True,
                                          label=_('Show the thumbnail strip'))

    class Meta:
        icon = 'image'
        label = _('Issue carousel')
        template = 'blocks/issue_carousel_block.html'
        group = _('Issue')

    def get_context(self, value, parent_context=None):
        context = super().get_context(value, parent_context)
        ctx = self.render_context(parent_context)
        issue = self.resolve_issue(value, ctx)
        cards = []

        if value.get('filter_by') == 'curated':
            cards = curation.cards_from_picks(value.get('picks'), ctx)
        elif issue is not None:
            articles = issue.get_all_articles()
            mode = value.get('filter_by')
            if mode == 'same_topic' and issue.topic_id:
                articles = [a for a in articles if a.topic_id == issue.topic_id]
            elif mode == 'other_topics':
                articles = [a for a in articles
                            if issue.topic_id and a.topic_id != issue.topic_id]
            # Re-fetch through the prefetched queryset: get_all_articles()
            # returns a plain list built from two unoptimised querysets, so
            # rendering it directly costs a query per byline.
            ids = [a.pk for a in articles]
            cards = [curation.HomeCard(a)
                     for a in curation.in_id_order(curation.article_queryset(), ids)]

        ctx.mark_seen(cards)
        context['issue'] = issue
        context['cards'] = cards
        context['sg_render_ctx'] = ctx
        return context


class OtherTopicsBlock(IssueSourceMixin, SectionBlock):
    heading = blocks.CharBlock(required=False, max_length=120, label=_('Heading'))
    max_items = blocks.IntegerBlock(default=12, min_value=1, max_value=40, required=False,
                                    label=_('Maximum items'))

    class Meta:
        icon = 'list-ul'
        label = _('Other topics in the issue')
        template = 'blocks/other_topics_block.html'
        group = _('Issue')
        # Scrolls inside its cell instead of stretching the grid row — see
        # the .sg-cell--scrolls rules in home.css.
        scrolls = True

    def get_context(self, value, parent_context=None):
        context = super().get_context(value, parent_context)
        ctx = self.render_context(parent_context)
        issue = self.resolve_issue(value, ctx)
        cards = []
        # With no topic on the issue there is no "other" to show — the same
        # conclusion the old template reached by leaving other_topic_articles
        # empty whenever current_issue.topic was unset.
        if issue is not None and issue.topic_id:
            articles = [a for a in issue.get_all_articles()
                        if a.topic_id != issue.topic_id]
            ids = [a.pk for a in articles][: value.get('max_items') or 12]
            cards = [curation.HomeCard(a, size='compact')
                     for a in curation.in_id_order(curation.article_queryset(), ids)]
            ctx.mark_seen(cards)
        context['issue'] = issue
        context['cards'] = cards
        context['sg_render_ctx'] = ctx
        return context


# ── Feeds ───────────────────────────────────────────────────────────────

class LatestArticlesBlock(ItemGroupBlock):
    kind = 'article'

    class Meta:
        icon = 'doc-full-inverse'
        label = _('Article feed')
        template = 'blocks/article_group_block.html'
        group = _('Feeds')


class PastIssuesBlock(ItemGroupBlock):
    kind = 'issue'

    class Meta:
        icon = 'copy'
        label = _('Issue feed')
        template = 'blocks/issue_group_block.html'
        group = _('Feeds')


class MostReadBlock(SectionBlock):
    heading = blocks.CharBlock(required=False, max_length=120, label=_('Heading'))
    scope = blocks.ChoiceBlock(
        choices=[
            ('articles', _('Articles')),
            ('issues',   _('Issues')),
            ('authors',  _('Authors')),
        ],
        default='articles', required=False, label=_('Most read'),
    )
    window = blocks.ChoiceBlock(choices=WINDOW_CHOICES, default='30',
                                required=False, label=_('Window'))
    count = blocks.IntegerBlock(default=5, min_value=1, max_value=20, required=False,
                                label=_('How many'))
    numbered = blocks.BooleanBlock(required=False, default=True,
                                   label=_('Show rank numbers'))

    class Meta:
        icon = 'view'
        label = _('Most read')
        template = 'blocks/most_read_block.html'
        group = _('Feeds')
        scrolls = True

    _MODELS = {
        'articles': ('articles.Article', curation.article_queryset),
        'issues':   ('issue.Issue',      curation.issue_queryset),
        'authors':  ('literati.Literati', curation.literati_queryset),
    }

    def get_context(self, value, parent_context=None):
        context = super().get_context(value, parent_context)
        ctx = self.render_context(parent_context)
        label, qs_factory = self._MODELS.get(value.get('scope') or 'articles',
                                             self._MODELS['articles'])
        count = value.get('count') or 5
        ids = curation.most_read_ids(label, int(value.get('window') or 30), count + 10)
        ids = [i for i in ids if i not in ctx.seen_pages][:count]
        cards = [curation.HomeCard(p, size='compact')
                 for p in curation.in_id_order(qs_factory(), ids)]
        ctx.mark_seen(cards)
        context['cards'] = cards
        context['sg_render_ctx'] = ctx
        return context


# ── Free-form ───────────────────────────────────────────────────────────

class CustomCardBlock(blocks.StructBlock):
    """A card with no model behind it — the escape hatch for promos,
    announcements, partner tiles and campaign units."""

    image = ImageChooserBlock(required=False, label=_('Image'))
    kicker = blocks.CharBlock(required=False, max_length=40, label=_('Kicker'))
    heading = blocks.CharBlock(required=False, max_length=160, label=_('Heading'))
    text = blocks.RichTextBlock(required=False, label=_('Text'))
    link_page = blocks.PageChooserBlock(required=False, label=_('Link to a page'))
    link_url = blocks.CharBlock(required=False, max_length=255,
                                label=_('…or an external URL'))
    link_label = blocks.CharBlock(required=False, max_length=60,
                                  label=_('Link label'))
    accent_colour = ColorPickerBlock(required=False, label=_('Accent colour'))

    class Meta:
        icon = 'form'
        label = _('Custom card')


class CardGroupBlock(SectionBlock):
    heading = blocks.CharBlock(required=False, max_length=120, label=_('Heading'))
    cards = blocks.ListBlock(CustomCardBlock(), required=False, label=_('Cards'))
    layout = blocks.ChoiceBlock(choices=LAYOUT_CHOICES, default='grid',
                                required=False, label=_('Layout'))
    columns = blocks.IntegerBlock(default=3, min_value=1, max_value=6, required=False,
                                  label=_('Columns'))

    class Meta:
        icon = 'form'
        label = _('Card group')
        template = 'blocks/card_group_block.html'
        group = _('Free-form')


class ImageBannerBlock(SectionBlock):
    image = ImageChooserBlock(required=False, label=_('Image'))
    alt_text = blocks.CharBlock(required=False, max_length=160, label=_('Alt text'))
    overlay_heading = blocks.CharBlock(required=False, max_length=160,
                                       label=_('Overlay heading'))
    overlay_text = blocks.TextBlock(required=False, label=_('Overlay text'))
    link_page = blocks.PageChooserBlock(required=False, label=_('Link to a page'))
    link_url = blocks.CharBlock(required=False, max_length=255,
                                label=_('…or an external URL'))

    class Meta:
        icon = 'image'
        label = _('Image banner')
        template = 'blocks/image_banner_block.html'
        group = _('Free-form')


class CtaButtonBlock(SectionBlock):
    label_text = blocks.CharBlock(required=False, max_length=60,
                                  label=_('Button label'))
    link_page = blocks.PageChooserBlock(required=False, label=_('Link to a page'))
    link_url = blocks.CharBlock(required=False, max_length=255,
                                label=_('…or an external URL'))
    style = blocks.ChoiceBlock(
        choices=[('primary', _('Primary')), ('secondary', _('Secondary'))],
        default='primary', required=False, label=_('Style'),
    )
    align = blocks.ChoiceBlock(
        choices=[('left', _('Left')), ('center', _('Centre')), ('right', _('Right'))],
        default='center', required=False, label=_('Alignment'),
    )

    class Meta:
        icon = 'link'
        label = _('Button')
        template = 'blocks/cta_button_block.html'
        group = _('Free-form')


class SubscribeBannerBlock(SectionBlock):
    kicker = blocks.CharBlock(required=False, max_length=60, label=_('Kicker'))
    heading = blocks.CharBlock(required=False, max_length=160, label=_('Heading'))
    cta_label = blocks.CharBlock(required=False, max_length=60,
                                 label=_('Button label'))
    subscriber_cta_label = blocks.CharBlock(
        required=False, max_length=60, label=_('Button label for subscribers'),
    )
    hide_for_subscribers = blocks.BooleanBlock(
        required=False, default=True, label=_('Hide from existing subscribers'),
    )

    class Meta:
        icon = 'mail'
        label = _('Subscribe banner')
        template = 'blocks/subscribe_banner_block.html'
        group = _('Free-form')


class QuoteBlock(SectionBlock):
    quote = blocks.TextBlock(required=False, label=_('Quote'))
    attribution = blocks.CharBlock(required=False, max_length=120,
                                   label=_('Attribution'))

    class Meta:
        icon = 'openquote'
        label = _('Pull quote')
        template = 'blocks/home_quote_block.html'
        group = _('Free-form')


class SpacerBlock(blocks.StructBlock):
    height = blocks.ChoiceBlock(
        choices=[('sm', _('Small')), ('md', _('Medium')), ('lg', _('Large'))],
        default='md', required=False, label=_('Height'),
    )

    class Meta:
        icon = 'horizontalrule'
        label = _('Spacer')
        template = 'blocks/home_spacer_block.html'
        group = _('Layout')


# ── The catalogue ───────────────────────────────────────────────────────
#
# Order here is the order of the canvas's "Add section" palette.  The
# `group` on each block's Meta is what the palette groups by.

HOMEPAGE_BLOCKS = [
    # Curated
    ('featured_article',  FeaturedArticleBlock()),
    ('article_group',     ArticleGroupBlock()),
    ('issue_group',       IssueGroupBlock()),
    ('author_spotlight',  AuthorSpotlightBlock()),
    ('topic_row',         TopicRowBlock()),

    # Issue
    ('issue_cover',       IssueCoverBlock()),
    ('issue_carousel',    IssueCarouselBlock()),
    ('other_topics',      OtherTopicsBlock()),

    # Feeds
    ('latest_articles',   LatestArticlesBlock()),
    ('past_issues',       PastIssuesBlock()),
    ('most_read',         MostReadBlock()),

    # Free-form
    ('subscribe_banner',  SubscribeBannerBlock()),
    ('card_group',        CardGroupBlock()),
    ('image_banner',      ImageBannerBlock()),
    ('cta_button',        CtaButtonBlock()),
    ('quote',             QuoteBlock()),
    ('rich_text',         blocks.RichTextBlock(
        required=False, label=_('Rich text'), icon='pilcrow',
        template='blocks/home_rich_text_block.html', group=_('Free-form'))),
    ('embed',             EmbedBlock(
        required=False, label=_('Embed'), icon='media', group=_('Free-form'),
        help_text=_('Paste a YouTube, Vimeo or SoundCloud URL.'))),
    ('raw_html',          blocks.RawHTMLBlock(
        required=False, label=_('Raw HTML'), icon='code', group=_('Free-form'),
        help_text=_('Not sanitised — use with care.'))),

    # Layout
    ('divider',           blocks.StaticBlock(
        admin_text=_('A horizontal rule.'), label=_('Divider'),
        icon='horizontalrule', group=_('Layout'),
        template='blocks/divider_block.html')),
    ('spacer',            SpacerBlock()),
]
