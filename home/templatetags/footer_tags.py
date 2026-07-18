# footer_tags.py
# ─────────────────────────────────────────────────────────────────────────
# Custom template tags for rendering global navigational site footer layers
# ─────────────────────────────────────────────────────────────────────────

from django import template
from django.urls import reverse, NoReverseMatch
from ..models import SiteFooter
from datetime import timedelta
from django.utils import timezone

register = template.Library()


def _inject_dynamic_urls(footer, dynamic_url_map):
    """
    Walks the prefetched footer hierarchy and injects each resolved dynamic
    URL onto its link instance as _resolved_dynamic_url.

    FooterColumnLink.get_url() reads that attribute — but only when
    dynamic_url_key is set (models.py).  Links without a dynamic key are
    therefore completely unaffected by this pass; their destination is
    worked out entirely inside get_url(), which is also where the mailto:
    and dead-link rules live.

    Because the hierarchy is prefetched, this runs entirely in Python —
    zero extra database queries.
    """
    if not footer:
        return

    for section in footer.sections.all():
        for col in section.columns.all():
            for link in col.column_links.all():
                if not link.dynamic_url_key:
                    continue

                # Only assign when something actually resolved.  Assigning
                # None would be harmless given get_url()'s current
                # `if self.dynamic_url_key and resolved:` test, but it makes
                # the attribute mean two different things — "unavailable" and
                # "never set" — so keep it absent when there is no value.
                resolved = dynamic_url_map.get(link.dynamic_url_key)
                if resolved:
                    link._resolved_dynamic_url = resolved


@register.simple_tag
def get_site_footer():
    """
    Compiles and provides global site navigation structures, including
    dynamic page mappings, metrics lookups, and editorial contexts.

    Dynamic URL injection
    ─────────────────────
    After all URLs are resolved, _inject_dynamic_urls() iterates over every
    FooterColumnLink in the prefetched hierarchy and — when the link has a
    dynamic_url_key set — injects the resolved URL as the private attribute
    _resolved_dynamic_url.  FooterColumnLink.get_url() checks for that
    attribute first, so the template calls {{ link.get_url }} as normal
    without needing any template-context gymnastics.

    Dead links and email addresses are NOT handled here — that normalisation
    belongs to FooterColumnLink.get_url() in models.py, because get_url()
    rewrites the raw url field (prepending '/' to bare paths) before any
    caller sees it.  Anything attempting to post-process get_url()'s output
    from out here is reading an already-transformed value.

    To add a new dynamic destination:
      1. Add a choice to DYNAMIC_URL_CHOICES in models.py.
      2. Add a matching key in the dynamic_url_map dict below.
      That's it — no template changes needed.
    """
    from django.apps import apps

    # Prefetch the nested navigation hierarchy blocks to maximise query performance
    footer = SiteFooter.objects.prefetch_related(
        "sections__columns__column_links__page"
    ).first()

    try:
        # NOTE: AuthorIndexPage is not used anywhere below.  Every model in
        # this block is a failure point — one stale app label aborts the whole
        # tag through the LookupError branch and every dynamic footer link
        # silently falls back to its fixed page/url.  Worth deleting once
        # confirmed unnecessary.
        AuthorIndexPage  = apps.get_model('literati', 'AuthorIndexPage')
        ArticleIndexPage = apps.get_model('articles', 'ArticleIndexPage')
        IssueIndexPage   = apps.get_model('issue',    'IssueIndexPage')
        Issue            = apps.get_model('issue',    'Issue')
        EditorialBoard   = apps.get_model('literati', 'EditorialBoard')
    except LookupError:
        # No dynamic URLs available — get_url() still falls back to each
        # link's fixed page/url, and still sends dead ones to DEAD_LINK_URL.
        return {'footer': footer, 'editorial_board': None}

    # ── Dynamic page URLs from database models ────────────────────────────
    latest_issue_page      = IssueIndexPage.objects.live().filter(sort_by='latest').first()
    latest_article_page    = ArticleIndexPage.objects.live().filter(sort_by='latest').first()
    most_read_issue_page   = IssueIndexPage.objects.live().filter(sort_by='most_read').first()
    most_read_article_page = ArticleIndexPage.objects.live().filter(sort_by='most_read').first()

    # ── Non-page app views (resolved dynamically via URL name) ───────────
    # NOTE: each fallback below is a literal path that must match what is
    # actually mounted in urls.py.  Two of them did not:
    #   most_read_topics  → '/topics/most_read/'      (no topics/ mount exists)
    #   archive_list      → '/the_librarian/archive/' (mounted at librarian/)
    # A silent NoReverseMatch plus a wrong fallback is indistinguishable from
    # a working link until someone clicks it.
    try:
        most_read_topics_url = reverse('most_read_topics')
    except NoReverseMatch:
        most_read_topics_url = '/topics/most_read/'

    # Added: Dynamically resolve the author analytics view route name
    try:
        most_read_authors_url = reverse('most_read_authors')
    except NoReverseMatch:
        most_read_authors_url = '/authors/most-read/'

    # ── Current and previous issue tracking ──────────────────────────────
    current_issue  = Issue.objects.live().order_by('-date_of_publishing').first()
    previous_issue = Issue.objects.live().order_by('-date_of_publishing')[1:2].first()

    # ── Editorial layout data assembly ────────────────────────────────────
    # The footer shows the *sitting* board (EditorialBoard.is_current) rather
    # than deriving it from the latest issue — a new board appears here the
    # moment it is marked current, before its first issue is published, and
    # a departed member drops out immediately instead of at the next issue.
    # Until one board is ticked "current", this footer section simply hides.
    editorial_board = None
    current_board = EditorialBoard.objects.filter(is_current=True).first()
    if current_board:
        active_members = list(
            current_board.members
            .filter(is_active=True)
            .select_related('editor', 'editor__profile_image')
        )
        role_map = [
            ('ചീഫ് എഡിറ്റർ',        'editor'),
            ('മാനേജിംഗ് എഡിറ്റർ',   'managing'),
            ('അസോസിയേറ്റ് എഡിറ്റർ', 'associate'),
            ('സമിതി അംഗങ്ങൾ',       'board'),
        ]
        sections = []
        for heading, role in role_map:
            members_list = [m for m in active_members if m.role == role]
            if members_list:
                sections.append({'heading': heading, 'members': members_list})

        if sections:
            editorial_board = {'sections': sections, 'board': current_board}

    # Added: Dynamically resolve the archive list view route name
    try:
        archive_list_url = reverse('the_librarian:archive_list')
    except NoReverseMatch:
        archive_list_url = '/librarian/archive/'   # was '/the_librarian/archive/'

    # Added: Dynamically resolve the postbox view route name
    try:
        postbox_url = reverse('postbox:postbox_page')
    except NoReverseMatch:
        postbox_url = '/postbox/'

    # ── Build the dynamic URL map ─────────────────────────────────────────
    # Keys must exactly match the choice values in DYNAMIC_URL_CHOICES
    # (models.py).  A None value means the destination is unavailable right
    # now (e.g. no previous issue exists yet); _resolved_dynamic_url is then
    # left unset and get_url() falls through to the page/url fallback the
    # editor configured.  If that fallback is also empty, get_url() returns
    # DEAD_LINK_URL rather than an empty href.
    dynamic_url_map = {
        'current_issue_url':     current_issue.url  if current_issue  else None,
        'previous_issue_url':    previous_issue.url if previous_issue else None,
        'latest_issue_url':      latest_issue_page.url   if latest_issue_page   else '/issues/',
        'most_read_issue_url':   most_read_issue_page.url   if most_read_issue_page   else None,
        'latest_article_url':    latest_article_page.url if latest_article_page else '/articles/',
        'most_read_article_url': most_read_article_page.url if most_read_article_page else None,
        'most_read_topics_url':   most_read_topics_url,
        'most_read_authors_url':  most_read_authors_url,
        'archive_list_url':       archive_list_url,
        'postbox_url':            postbox_url,
    }

    # ── Inject resolved URLs into prefetched link instances ───────────────
    _inject_dynamic_urls(footer, dynamic_url_map)

    # Return structured context into the rendering runtime
    return {
        'footer':                  footer,
        'latest_issue_url':        dynamic_url_map['latest_issue_url'],
        'latest_article_url':      dynamic_url_map['latest_article_url'],
        'most_read_issue_url':     dynamic_url_map['most_read_issue_url'],
        'most_read_article_url':   dynamic_url_map['most_read_article_url'],
        'most_read_topics_url':     most_read_topics_url,
        'most_read_authors_url':    most_read_authors_url,  # Added: Pass down to template runtime context parameters
        'current_issue_url':       dynamic_url_map['current_issue_url'],
        'previous_issue_url':      dynamic_url_map['previous_issue_url'],
        'editorial_board':         editorial_board,
    }