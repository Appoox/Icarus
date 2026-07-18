# ─────────────────────────────────────────────────────────────────────────
# Project-level views that do not belong to any single app
# ─────────────────────────────────────────────────────────────────────────

from django.conf import settings
from django.shortcuts import render


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
    response = render(request, 'under_construction.html', status=status)

    if status == 503:
        # Ask crawlers to come back in a day instead of dropping the URL.
        # Only meaningful on 503 — ignored on 404.
        response['Retry-After'] = '86400'

    return response
