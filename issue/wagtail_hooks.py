from wagtail import hooks
from wagtail.models import Page
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