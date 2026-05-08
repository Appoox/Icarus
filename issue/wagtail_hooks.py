from wagtail import hooks

@hooks.register('construct_explorer_page_queryset')
def order_issues_in_explorer(parent_page, pages, request):
    """
    Ensure that when viewing the children of an IssueIndexPage (i.e. Issue pages)
    in the Wagtail admin explorer or chooser modal, they are ordered newest first.
    """
    if parent_page.specific_class.__name__ == 'IssueIndexPage':
        pages = pages.order_by('-first_published_at')
    return pages
