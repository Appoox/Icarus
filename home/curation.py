"""
home/curation.py
─────────────────────────────────────────────────────────────────────────
Content resolution for the homepage layout canvas.

Every homepage feed block answers the same question — "which items go in
this section?" — and the answer can come from three places:

    auto              a queryset (latest / most-read / this issue / by topic…)
    manual            an ordered list the editor picked by hand
    pinned_then_auto  the picks first, then the queryset tops it up

That logic lives here rather than in blocks.py so the blocks stay
declarative and the resolution is testable on its own.

Two things are worth understanding before changing anything in here:

1. HomeCard is a *presentation* wrapper, not a model.  A curated pick can
   override the headline, image, kicker and accent colour of the page it
   points at, so the templates must never read `article.title` directly —
   they read `card.title`, which is the override when there is one and the
   page's own value when there isn't.  This is what lets the same article
   lead the homepage under a punchier headline without editing the article.

2. Resolution is *order-dependent* by design.  Blocks are resolved in the
   order they appear on the grid (top-left to bottom-right, see
   HomePage.get_context), threading one RenderContext through all of them.
   Each block records what it displayed in ctx.seen_pages, and later blocks
   with `exclude_shown` set skip those.  That reproduces the behaviour the
   old hardcoded template had — `latest_articles` excluded the current
   issue's articles — while letting sections sit anywhere on the page.
"""
from collections import defaultdict
from datetime import timedelta

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count
from django.utils import timezone

from hitcount.models import Hit
from wagtail.models import Page as WagtailPage


# ── Card ────────────────────────────────────────────────────────────────

class HomeCard:
    """
    One renderable item on the homepage.

    Wraps a live Page (Article / Issue / Literati) and layers the optional
    per-placement overrides from a CuratedItemBlock on top.  Templates read
    only this object, never the underlying page, so an auto-queried item and
    a hand-picked one with three overrides render through exactly the same
    partial.
    """

    __slots__ = (
        'page', 'size',
        '_headline', '_standfirst', '_image', '_kicker', '_badge', '_accent',
    )

    def __init__(self, page, *, headline=None, standfirst=None, image=None,
                 kicker=None, badge=None, accent=None, size='medium'):
        self.page = page
        self.size = size or 'medium'
        self._headline = (headline or '').strip()
        self._standfirst = (standfirst or '').strip()
        self._image = image
        self._kicker = (kicker or '').strip()
        self._badge = (badge or '').strip()
        self._accent = (accent or '').strip()

    # ── Identity ────────────────────────────────────────────────────────
    @property
    def pk(self):
        return self.page.pk

    @property
    def url(self):
        # Page.url is None for a page with no routable site path, which would
        # otherwise render as the literal string "None" in an href.
        return self.page.url or ''

    # ── Overridable presentation ────────────────────────────────────────
    @property
    def title(self):
        return self._headline or self.page.title

    @property
    def standfirst(self):
        return self._standfirst

    @property
    def image(self):
        # `cover_image` exists on Article and Issue; Literati uses
        # `profile_image`.
        return (
            self._image
            or getattr(self.page, 'cover_image', None)
            or getattr(self.page, 'profile_image', None)
        )

    @property
    def kicker(self):
        return self._kicker

    @property
    def badge(self):
        return self._badge

    @property
    def topic(self):
        return getattr(self.page, 'topic', None)

    @property
    def accent(self):
        """Explicit override → the topic's colour → the house red."""
        if self._accent:
            return self._accent
        topic = self.topic
        if topic is not None and getattr(topic, 'color', None):
            return topic.color
        return '#c8102e'

    @property
    def date(self):
        return (
            getattr(self.page, 'date', None)
            or getattr(self.page, 'date_of_publishing', None)
            or self.page.first_published_at
        )

    @property
    def authors(self):
        rels = getattr(self.page, 'article_authors', None)
        if rels is None:
            return []
        return [r.author for r in rels.all() if r.author_id]

    def __repr__(self):
        return f'<HomeCard {self.page.pk} {self.title[:30]!r}>'


# ── Render context ──────────────────────────────────────────────────────

class RenderContext:
    """
    Threaded through every block on one homepage render.

    `seen_pages` is what makes `exclude_shown` work across independently
    placed sections; `_issue_cache` stops six blocks each running their own
    "what is the current issue?" query.
    """

    def __init__(self, request):
        self.request = request
        self.seen_pages = set()
        self._issue_cache = {}

    def mark_seen(self, cards):
        for card in cards:
            self.seen_pages.add(card.pk)

    def mark_page_seen(self, page):
        """Record a page shown outside a card — the issue an issue block is
        built around, for instance, so a later issue feed does not repeat it."""
        if page is not None:
            self.seen_pages.add(page.pk)

    def current_issue(self):
        if 'current' not in self._issue_cache:
            Issue = apps.get_model('issue', 'Issue')
            self._issue_cache['current'] = (
                Issue.objects.live()
                .select_related('topic', 'cover_image')
                .order_by('-date_of_publishing')
                .first()
            )
        return self._issue_cache['current']


# ── Optimised base querysets ────────────────────────────────────────────
#
# The old homepage did `article.article_authors.all` and `article.topic` per
# card with no prefetching — an N+1 on every request, and nothing in this
# stack caches rendered HTML.  Every queryset below is prefetched at source
# so the card partials stay free.

def article_queryset():
    Article = apps.get_model('articles', 'Article')
    return (
        Article.objects.live()
        .select_related('topic', 'cover_image', 'main_issue')
        .prefetch_related('article_authors__author')
    )


def issue_queryset():
    Issue = apps.get_model('issue', 'Issue')
    return (
        Issue.objects.live()
        .select_related('topic', 'cover_image', 'volume')
    )


def literati_queryset():
    Literati = apps.get_model('literati', 'Literati')
    return Literati.objects.live().select_related('profile_image')


# ── Most-read ───────────────────────────────────────────────────────────

def most_read_ids(model_label, days, limit):
    """
    Return page IDs ordered by hit count over a rolling window.

    Mirrors the aggregation in issue/views.py:most_read_topics — including
    the reason it filters on two content types.  django-hitcount keys hits
    by the content type it was handed, and this project counts hits from
    both the concrete model and wagtailcore.Page, so a single-CT filter
    silently loses roughly half the traffic.

    `days=0` means all time.
    """
    model = apps.get_model(*model_label.split('.'))
    live_pks = [str(pk) for pk in model.objects.live().values_list('pk', flat=True)]
    if not live_pks:
        return []

    try:
        model_ct = ContentType.objects.get_for_model(model)
        page_ct = ContentType.objects.get_for_model(WagtailPage)
    except LookupError:
        return []

    hits = Hit.objects.filter(
        hitcount__content_type__in=[model_ct, page_ct],
        hitcount__object_pk__in=live_pks,
    )
    if days:
        hits = hits.filter(created__gte=timezone.now() - timedelta(days=days))

    totals = defaultdict(int)
    for row in hits.values('hitcount__object_pk').annotate(n=Count('id')):
        try:
            totals[int(row['hitcount__object_pk'])] += row['n']
        except (TypeError, ValueError):
            continue

    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    return [pk for pk, _ in ranked[:limit]]


def in_id_order(queryset, ids):
    """Fetch `ids` and return them in the given order, dropping any that
    are no longer live (an article unpublished after it was ranked)."""
    by_pk = {obj.pk: obj for obj in queryset.filter(pk__in=ids)}
    return [by_pk[pk] for pk in ids if pk in by_pk]


# ── Curated picks ───────────────────────────────────────────────────────

def cards_from_picks(picks, ctx, default_size='medium'):
    """
    Turn a ListBlock of CuratedItemBlock values into HomeCards.

    A pick whose target has since been unpublished or deleted is skipped
    rather than raising — an editor's homepage should degrade, not 500,
    when someone unpublishes an article it points at.
    """
    cards = []
    for pick in picks or []:
        page = pick.get('item')
        if page is None:
            continue
        specific = page.specific if hasattr(page, 'specific') else page
        if not getattr(specific, 'live', False):
            continue
        cards.append(HomeCard(
            specific,
            headline=pick.get('headline_override'),
            standfirst=pick.get('standfirst'),
            image=pick.get('image_override'),
            kicker=pick.get('kicker'),
            badge=pick.get('badge'),
            accent=pick.get('accent_colour'),
            size=pick.get('size') or default_size,
        ))
    return cards


# ── Auto sources ────────────────────────────────────────────────────────

def _auto_articles(source, ctx, exclude_ids, limit):
    auto_from = source.get('auto_from') or 'latest'
    qs = article_queryset().exclude(pk__in=exclude_ids)

    if auto_from == 'most_read':
        days = int(source.get('window') or 30)
        # Over-fetch: some ranked IDs will be filtered out by `exclude_ids`.
        ids = most_read_ids('articles.Article', days, limit + len(exclude_ids) + 10)
        ids = [i for i in ids if i not in exclude_ids]
        return in_id_order(qs, ids)[:limit]

    if auto_from == 'current_issue':
        issue = ctx.current_issue()
        if issue is None:
            return []
        ids = [a.pk for a in issue.get_all_articles()]
        return in_id_order(qs, ids)[:limit]

    if auto_from == 'by_topic':
        topic = source.get('topic')
        if topic is None:
            return []
        qs = qs.filter(topic=topic)

    elif auto_from == 'by_author':
        author = source.get('author')
        if author is None:
            return []
        # PageChooserBlock hands back a base Page, so match on pk rather
        # than passing the instance into a Literati-typed FK lookup.
        qs = qs.filter(article_authors__author_id=author.pk).distinct()

    elif auto_from == 'by_volume':
        volume = source.get('volume')
        if volume is None:
            return []
        qs = qs.filter(main_issue__volume=volume)

    elif auto_from == 'by_issue':
        issue = source.get('issue')
        if issue is None:
            return []
        qs = qs.filter(main_issue_id=issue.pk)

    return list(qs.order_by('-first_published_at')[:limit])


def _auto_issues(source, ctx, exclude_ids, limit):
    auto_from = source.get('auto_from') or 'latest'
    qs = issue_queryset().exclude(pk__in=exclude_ids)

    if auto_from == 'most_read':
        days = int(source.get('window') or 30)
        ids = most_read_ids('issue.Issue', days, limit + len(exclude_ids) + 10)
        ids = [i for i in ids if i not in exclude_ids]
        return in_id_order(qs, ids)[:limit]

    if auto_from == 'by_topic':
        topic = source.get('topic')
        if topic is None:
            return []
        qs = qs.filter(topic=topic)

    elif auto_from == 'by_volume':
        volume = source.get('volume')
        if volume is None:
            return []
        qs = qs.filter(volume=volume)

    return list(qs.order_by('-date_of_publishing')[:limit])


def _auto_authors(source, ctx, exclude_ids, limit):
    qs = literati_queryset().exclude(pk__in=exclude_ids)
    if (source.get('auto_from') or 'latest') == 'most_read':
        days = int(source.get('window') or 30)
        ids = most_read_ids('literati.Literati', days, limit + len(exclude_ids) + 10)
        ids = [i for i in ids if i not in exclude_ids]
        return in_id_order(qs, ids)[:limit]
    return list(qs.order_by('-first_published_at')[:limit])


_AUTO_RESOLVERS = {
    'article': _auto_articles,
    'issue': _auto_issues,
    'author': _auto_authors,
}


# ── The entry point ─────────────────────────────────────────────────────

def resolve(source, ctx, *, kind='article', default_size='medium', limit=None):
    """
    Resolve one ContentSourceBlock value into an ordered list of HomeCards.

    `source` is the StructValue; `kind` selects which content type the auto
    half queries.  Returns at most `count` cards (or `limit`, when a block
    imposes its own ceiling — `featured_article` passes limit=1).

    Callers are responsible for calling ctx.mark_seen() on the result once
    they have committed to rendering it.  It is deliberately not done here:
    a block that resolves items and then decides not to render them must not
    poison the dedup set for the blocks below it.
    """
    if source is None:
        return []

    mode = source.get('source_mode') or 'auto'
    count = limit if limit is not None else int(source.get('count') or 6)
    if count <= 0:
        return []

    exclude_ids = set()
    if source.get('exclude_shown', True):
        exclude_ids |= ctx.seen_pages

    cards = []
    if mode in ('manual', 'pinned_then_auto'):
        for card in cards_from_picks(source.get('picks'), ctx, default_size):
            # A hand-picked item always wins over dedup — if an editor pinned
            # it here deliberately, showing it twice is their call, not ours.
            if card.pk not in {c.pk for c in cards}:
                cards.append(card)
        cards = cards[:count]

    if mode in ('auto', 'pinned_then_auto') and len(cards) < count:
        exclude_ids |= {c.pk for c in cards}
        resolver = _AUTO_RESOLVERS.get(kind, _auto_articles)
        for page in resolver(source, ctx, exclude_ids, count - len(cards)):
            cards.append(HomeCard(page, size=default_size))

    return cards[:count]
