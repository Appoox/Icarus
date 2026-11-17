# ─────────────────────────────────────────────────────────────────────────
# Project-level views that do not belong to any single app
# ─────────────────────────────────────────────────────────────────────────

import logging

from django.conf import settings
from django.shortcuts import render
from django.template import TemplateDoesNotExist, loader
from django.views import defaults

from home.theme import themed_template

logger = logging.getLogger(__name__)


def under_construction(request):
    """
    Friendly placeholder for footer destinations that are not built yet.

    Status code
    ───────────
    Defaults to 404, NOT 200.  A placeholder served as 200 is a textbook
    soft-404: Google indexes it as a real page, notices it has no unique
    content, and then flags it in Search Console — and because this page is
    reachable from the footer, that means from every URL on the site.
    Returning 404 keeps the visitor experience identical (they still get the
    full styled page, in Malayalam) while telling crawlers not to index it.

    Override per-environment with FOOTER_UNDER_CONSTRUCTION_STATUS:
      404  the section does not exist yet and has no committed launch date
      503  it is launching imminently and you want crawlers to retry rather
           than drop the URL — Retry-After is set automatically below
      200  only if the page has become real standalone content
    """
    status = getattr(settings, 'FOOTER_UNDER_CONSTRUCTION_STATUS', 404)

    # Template name is relative to the app/DIRS template root, so the file at
    # icarus/templates/under_construction.html is addressed as just
    # 'under_construction.html'
    response = render(request, themed_template(request, 'under_construction.html'), status=status)

    if status == 503:
        # Ask crawlers to come back in a day instead of dropping the URL.
        # Only meaningful on 503 — ignored on 404.
        response['Retry-After'] = '86400'

    return response


# ── Error pages ───────────────────────────────────────────────────────────
# Django's own handlers, pointed at the active site design's template.  In
# the Classic design these render exactly the templates Django would.


def _error_template(request, name):
    """The first of the themed names for `name` that exists."""
    for candidate in themed_template(request, name):
        try:
            loader.get_template(candidate)
            return candidate
        except TemplateDoesNotExist:
            continue
    return name


def _themed_error_page(handler, request, name, *args):
    """
    Django's `handler` with the active design's template.  The print
    design's 403/404 draw the whole site frame (masthead, section nav,
    current issue) from the database; if that fails, the plain Classic
    page is served instead of turning a 404 into a 500.
    """
    try:
        template_name = _error_template(request, name)
    except Exception:
        logger.exception('Could not look up the site design for %s', name)
        template_name = name
    if template_name == name:
        return handler(request, *args, template_name=name)
    try:
        return handler(request, *args, template_name=template_name)
    except Exception:
        logger.exception('Could not draw %s in the site design; using the Classic page', name)
        return handler(request, *args, template_name=name)


def permission_denied(request, exception):
    return _themed_error_page(defaults.permission_denied, request, '403.html', exception)


def page_not_found(request, exception):
    return _themed_error_page(defaults.page_not_found, request, '404.html', exception)


def server_error(request):
    # Something has already gone wrong, possibly the database: if the
    # design can't even be looked up, fall back to the Classic page.
    try:
        template_name = _error_template(request, '500.html')
    except Exception:
        template_name = '500.html'
    return defaults.server_error(request, template_name=template_name)
