from wagtail import hooks
from wagtail.admin.viewsets.pages import PageListingViewSet
from articles.wagtail_hooks import HitCountColumn, AnalyticsColumn
from .models import AuthorIndexPage
from wagtail.models import Page 

@hooks.register('construct_page_chooser_queryset')
def order_literati_in_chooser(pages, request):
    """
    Order literati pages newest-first inside the page chooser modal
    (e.g. when selecting an author from an article or any other page reference).
    """
    page_type = request.GET.get('page_type')
    is_target_chooser = False

    if page_type and 'literati' in page_type.lower():
        is_target_chooser = True
    else:
        parent_id = None
        if request.resolver_match and 'parent_page_id' in request.resolver_match.kwargs:
            parent_id = request.resolver_match.kwargs.get('parent_page_id')
        else:
            parent_id = request.GET.get('child_of') or request.GET.get('parent')
        
        if parent_id:
            try:
                parent = Page.objects.get(pk=parent_id)
                if parent.specific_class and issubclass(parent.specific_class, AuthorIndexPage):
                    is_target_chooser = True
                    
            except Page.DoesNotExist:
                pass

    if is_target_chooser:
        pages = pages.order_by('-latest_revision_created_at')
    return pages
    
class LiteratiPageListingViewSet(PageListingViewSet):
    icon = 'user'
    menu_label = 'Authors'
    menu_order = 201
    add_to_admin_menu = True

    @property
    def columns(self):
        return super().columns + [
            HitCountColumn("hit_count", label="Views", sort_key="hit_count_generic__hits"),
            AnalyticsColumn("read_fully_count", "read_fully_count", label="Read Fully", sort_key="read_fully_count")
        ]

@hooks.register('register_admin_viewset')
def register_literati_viewset():
    from .models import Literati
    LiteratiPageListingViewSet.model = Literati
    return LiteratiPageListingViewSet('literati')


import logging

logger = logging.getLogger(__name__)


@hooks.register('after_create_page')
def after_create_literati(request, page):
    """
    Literati.save() has already created (or linked) the author's reader
    account. If that account has no password yet, email the author a
    one-time link to choose one. No password is ever generated, shown,
    logged or sent.
    """
    from .models import Literati
    if not isinstance(page, Literati) or page.reader_user is None:
        return

    from django.contrib import messages
    from reader.password_setup import send_password_setup_email

    user = page.reader_user
    if user.has_usable_password():
        messages.info(request, f"{page.title} is linked to their existing reader account.")
        return

    if not page.email:
        messages.warning(
            request,
            f"A reader account was created for {page.title}, but there is no "
            f"email address to send a set-password link to. Add one, or ask "
            f"them to use \"Forgot password\" on the login page.",
        )
        return

    try:
        send_password_setup_email(user, name=page.title)
    except Exception:
        logger.exception("Could not send password set-up email for Literati pk=%s", page.pk)
        messages.error(
            request,
            f"A reader account was created for {page.title}, but the "
            f"set-password email could not be sent.",
        )
    else:
        messages.success(
            request,
            f"A reader account was created for {page.title} and a link to set "
            f"their password was emailed to them.",
        )
