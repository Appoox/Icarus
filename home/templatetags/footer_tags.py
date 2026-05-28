from django import template
from ..models import SiteFooter
from datetime import timedelta
from django.utils import timezone

register = template.Library()

@register.simple_tag
def get_site_footer():
    from django.apps import apps

    footer = SiteFooter.objects.prefetch_related("links").first()

    try:
        ArticleIndexPage = apps.get_model('articles', 'ArticleIndexPage')
        IssueIndexPage   = apps.get_model('issue',    'IssueIndexPage')
        Issue            = apps.get_model('issue',    'Issue')
    except LookupError:
        return {'footer': footer, 'editorial_board': None}

    # ── Dynamic page URLs ──────────────────────────────────────────────
    latest_issue_page      = IssueIndexPage.objects.live().filter(sort_by='latest').first()
    latest_article_page    = ArticleIndexPage.objects.live().filter(sort_by='latest').first()
    most_read_issue_page   = IssueIndexPage.objects.live().filter(sort_by='most_read').first()
    most_read_article_page = ArticleIndexPage.objects.live().filter(sort_by='most_read').first()

    # ── Current issue ──────────────────────────────────────────────────
    current_issue = Issue.objects.live().order_by('-date_of_publishing').first()

    # ── Editorial board from the current issue ─────────────────────────
    # Each key maps to a role heading label and the queryset of board members.
    # The Issue model's helper methods (editors_list, managing_editors_list,
    # associate_editors_list, board_members_only) are called here so the
    # footer always reflects the most recently published issue's board.
    editorial_board = None
    if current_issue:
        sections = []
        role_map = [
            ('എഡിറ്റർ',             'editors_list'),
            ('മാനേജിംഗ് എഡിറ്റർ',   'managing_editors_list'),
            ('അസോസിയേറ്റ് എഡിറ്റർ', 'associate_editors_list'),
            ('സമിതി അംഗങ്ങൾ',       'board_members_only'),
        ]
        for heading, attr in role_map:
            members = getattr(current_issue, attr, None)
            # attr may be a method or a cached property; call it if needed
            if callable(members):
                members = members()
            if members:
                # Convert to list to avoid re-evaluating lazy querysets in template
                members_list = list(members)
                if members_list:
                    sections.append({'heading': heading, 'members': members_list})

        if sections:
            editorial_board = {'sections': sections, 'issue': current_issue}

    return {
        'footer':                  footer,
        'latest_issue_url':        latest_issue_page.url   if latest_issue_page   else '/issues/',
        'latest_article_url':      latest_article_page.url if latest_article_page else '/articles/',
        'most_read_issue_url':     most_read_issue_page.url   if most_read_issue_page   else None,
        'most_read_article_url':   most_read_article_page.url if most_read_article_page else None,
        'current_issue_url':       current_issue.url if current_issue else None,
        'editorial_board':         editorial_board,
    }