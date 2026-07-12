from wagtail import hooks
from wagtail.models import Page
from wagtail.admin.ui.components import Component
from .models import Issue, IssueIndexPage

@hooks.register('construct_explorer_page_queryset')
def order_issues_in_explorer(parent_page, pages, request):
    """
    Order Issue pages newest-first in the Wagtail page explorer tree.
    """
    if parent_page.specific_class.__name__ == 'IssueIndexPage':
        pages = Issue.objects.filter(pk__in=pages.values_list('pk', flat=True)).order_by('-date_of_publishing')
    return pages


@hooks.register('construct_page_chooser_queryset')
def order_issues_in_chooser(pages, request):
    """
    Order Issue pages newest-first inside the page chooser modal
    (e.g. when selecting an issue from an article or any other page reference).
    """
    parent_id = None
    if request.resolver_match and 'parent_page_id' in request.resolver_match.kwargs:
        parent_id = request.resolver_match.kwargs.get('parent_page_id')
    else:
        parent_id = request.GET.get('child_of') or request.GET.get('parent')
    
    # Proceed only if a valid parent_id is extracted from the routing/request context
    if parent_id:
        try:
            parent = Page.objects.get(pk=parent_id)
            
            if parent.specific_class and issubclass(parent.specific_class, IssueIndexPage):
                pages = pages.order_by('-issue__date_of_publishing')
        except Page.DoesNotExist:
            pass
            
    # Return the evaluated queryset (either newly sorted or the original fallback)
    return pages


@hooks.register('construct_snippet_chooser_queryset')
def order_topics_in_chooser(snippet_type, queryset, request):
    """
    Order Topics newest-first in the snippet chooser modal.
    (The snippet listing page already respects Meta.ordering = ['-id'].)
    """
    if snippet_type.__name__ == 'Topic':
        queryset = queryset.order_by('-id')
    return queryset


# ── Audio coverage dashboard panel ────────────────────────────────────────

class AudioCoveragePanel(Component):
    """
    Wagtail dashboard panel tracking audio coverage: how many live issues
    and articles have audio attached, so generation gaps are visible at a
    glance.

    Detection:
      • Issue   — the dedicated audio_file document or audio_embed_url field.
      • Article — an 'audio' block in any of the four language body
        StreamFields. Articles have no top-level audio field, so this uses
        jsonb containment (body @> '[{"type": "audio"}]') — the count stays
        entirely in the database, no StreamField parsing per article. Note
        this counts the block's presence; an audio block saved with both
        fields left empty (rare) still counts.
    """
    order = 210  # directly after The Librarian's ingest panel (200)

    def render_html(self, parent_context=None):
        from django.db.models import Q
        from django.utils.safestring import mark_safe
        from articles.models import Article

        issues = Issue.objects.live()
        issue_total = issues.count()
        issue_with = issues.filter(
            Q(audio_file__isnull=False) |
            (Q(audio_embed_url__isnull=False) & ~Q(audio_embed_url=''))
        ).count()

        audio_block = [{'type': 'audio'}]
        articles = Article.objects.live()
        article_total = articles.count()
        article_with = articles.filter(
            Q(body__contains=audio_block) |
            Q(body_en__contains=audio_block) |
            Q(body_hi__contains=audio_block) |
            Q(body_ta__contains=audio_block)
        ).count()

        def pct(count, total):
            return round(100 * count / total) if total else 0

        def card(count, total, label):
            p = pct(count, total)
            return f'''
                <div style="background:var(--w-color-surface-field);border-radius:6px;padding:1em;text-align:center;min-width:170px;flex:1;max-width:260px;">
                    <div style="font-size:1.8em;font-weight:700;">
                        {count} <span style="font-size:.55em;font-weight:400;color:var(--w-color-text-meta);">/ {total}</span>
                    </div>
                    <div style="color:var(--w-color-text-meta);font-size:.8em;margin-top:.25em;">{label}</div>
                    <div style="background:var(--w-color-border-furniture);border-radius:3px;height:6px;margin-top:.6em;overflow:hidden;">
                        <div style="background:var(--w-color-primary);height:100%;width:{p}%;"></div>
                    </div>
                    <div style="color:var(--w-color-text-meta);font-size:.75em;margin-top:.35em;">{p}% covered</div>
                </div>'''

        return mark_safe(f'''
        <section class="panel summary nice-padding" style="margin-top:1.5em;">
            <div style="background:var(--w-color-surface-page);border:1px solid var(--w-color-border-furniture);border-radius:8px;padding:1.5em;">
                <h2 style="margin:0 0 .25em;font-size:1.1em;display:flex;align-items:center;gap:.4em;">
                    <svg class="icon" aria-hidden="true" style="width:1em;height:1em;"><use href="#icon-media"></use></svg>
                    Audio Coverage
                </h2>
                <p style="color:var(--w-color-text-meta);font-size:.85em;margin:0 0 1em;">
                    Live issues and articles with audio attached — issues via
                    their audio field, articles via an audio block in any
                    language body.
                </p>
                <div style="display:flex;gap:1em;flex-wrap:wrap;justify-content:center;">
                    {card(issue_with, issue_total, "Issues with audio")}
                    {card(article_with, article_total, "Articles with audio")}
                </div>
            </div>
        </section>
        ''')


@hooks.register("construct_homepage_panels")
def add_audio_coverage_panel(request, panels):
    panels.append(AudioCoveragePanel())