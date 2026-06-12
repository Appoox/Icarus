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

@register.simple_tag
def get_site_footer():
    """
    Compiles and provides global site navigation structures, including
    dynamic page mappings, metrics lookups, and editorial contexts.

    Dynamic URL injection
    ─────────────────────
    After all URLs are resolved, this function iterates over every
    FooterColumnLink in the prefetched hierarchy and — when the link has a
    dynamic_url_key set — injects the resolved URL as the private attribute
    _resolved_dynamic_url.  FooterColumnLink.get_url() checks for that
    attribute first, so the template calls {{ link.get_url }} as normal
    without needing any template-context gymnastics.

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
        AuthorIndexPage  = apps.get_model('literati', 'AuthorIndexPage')
        ArticleIndexPage = apps.get_model('articles', 'ArticleIndexPage')
        IssueIndexPage   = apps.get_model('issue',    'IssueIndexPage')
        Issue            = apps.get_model('issue',    'Issue')
    except LookupError:
        return {'footer': footer, 'editorial_board': None}

    # ── Dynamic page URLs from database models ────────────────────────────
    latest_issue_page      = IssueIndexPage.objects.live().filter(sort_by='latest').first()
    latest_article_page    = ArticleIndexPage.objects.live().filter(sort_by='latest').first()
    most_read_issue_page   = IssueIndexPage.objects.live().filter(sort_by='most_read').first()
    most_read_article_page = ArticleIndexPage.objects.live().filter(sort_by='most_read').first()

    # ── Non-page app views (resolved dynamically via URL name) ───────────
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
    editorial_board = None
    if current_issue:
        sections = []
        role_map = [
            ('ചീഫ് എഡിറ്റർ',        'editors_list'),
            ('മാനേജിംഗ് എഡിറ്റർ',   'managing_editors_list'),
            ('അസോസിയേറ്റ് എഡിറ്റർ', 'associate_editors_list'),
            ('സമിതി അംഗങ്ങൾ',       'board_members_only'),
        ]
        for heading, attr in role_map:
            members = getattr(current_issue, attr, None)
            if callable(members):
                members = members()
            if members:
                members_list = list(members)
                if members_list:
                    sections.append({'heading': heading, 'members': members_list})

        if sections:
            editorial_board = {'sections': sections, 'issue': current_issue}

    try:
        archive_list_url = reverse('the_librarian:archive_list')
    except NoReverseMatch:
        archive_list_url = '/the_librarian/archive/'
        
    # ── Build the dynamic URL map ─────────────────────────────────────────
    # Keys must exactly match the choice values in DYNAMIC_URL_CHOICES
    # (models.py).  A None value means the destination is unavailable right
    # now (e.g. no previous issue exists yet); get_url() falls through to
    # the page/url fallback so the link is still rendered, just pointing
    # wherever the editor configured as a fallback.
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
    }

    # ── Inject resolved URLs into prefetched link instances ───────────────
    # Because footer.sections is already prefetched, this loop runs entirely
    # in Python — zero extra database queries.
    # FooterColumnLink.get_url() reads _resolved_dynamic_url via getattr(),
    # so links without a dynamic_url_key are completely unaffected.
    if footer:
        for section in footer.sections.all():
            for col in section.columns.all():
                for link in col.column_links.all():
                    if link.dynamic_url_key:
                        # Inject the resolved URL; None means unavailable —
                        # get_url() will fall back to page/url in that case
                        link._resolved_dynamic_url = dynamic_url_map.get(
                            link.dynamic_url_key
                        )

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