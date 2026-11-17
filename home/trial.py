"""
home/trial.py
─────────────────────────────────────────────────────────────────────────
Trial mode: while the site is being tried out, readers can open only the
issues the editors have picked; editors see everything.

Switched on, with the issues picked and the wording, under Wagtail
admin → Settings → Trial mode (home.models.TrialModeSettings).
Switching it off puts everything back as it was: no content changes.

While it is on, anyone who is not an editor gets the "not available"
page (a 404 listing the picked issues) for:

  * an issue page that isn't picked;
  * an article whose issue isn't picked (published or not), unless it is
    reprinted in a picked issue.  Articles with no issue stay open;
  * an archive scan whose month has a published issue page that isn't
    picked, by any of its addresses (flipbook, PDF, page images, its
    search-result viewer, the /media/archive/ file).  Scans with no
    published issue page stay open.

Everything else stays open, and every list (homepage, issue list,
authors, topics, search, archive) shows what it always shows: a closed
issue is listed, but opening it gives the "not available" page.

Everyone, editors included, sees a banner saying the site is on trial
(includes/trial_banner.html); editors also see what readers can open.

Editors are superusers, staff (which includes the current editorial
board, see literati.sync_editorial_staff) and anyone who may use the
Wagtail admin.

The gate has three parts: the before_serve_page hook in
home/wagtail_hooks.py (issue and article pages), TrialModeMiddleware
(the archive's Django views) and Icarus.protected_media.archive_authz
(the scan files Caddy serves).
"""
from django.db.models import Q
from django.shortcuts import render
from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext as _

# the_librarian views that show an archive scan, by their URL keyword.
SCAN_VIEWS = {
    'magazine_viewer',
    'serve_magazine_pdf',
    'download_magazine_pdf',
    'magazine_page_meta',
    'magazine_page_image',
}
DOCUMENT_VIEWS = {
    'viewer',
    'serve_pdf',
    'document_page_meta',
    'document_page_image',
}

EDITOR_PERMISSION = 'wagtailadmin.access_admin'


# ── Who sees what ────────────────────────────────────────────────────────

def is_editor(user):
    """May see everything while trial mode is on."""
    if not getattr(user, 'is_authenticated', False) or not user.is_active:
        return False
    return bool(user.is_superuser or user.is_staff or user.has_perm(EDITOR_PERMISSION))


def get_settings(request):
    """This site's TrialModeSettings, or None. Cached on the request."""
    if not hasattr(request, '_trial_settings'):
        from wagtail.models import Site
        from home.models import TrialModeSettings

        site = Site.find_for_request(request)
        request._trial_settings = TrialModeSettings.for_site(site) if site else None
    return request._trial_settings


def is_on(request):
    settings_obj = get_settings(request)
    return bool(settings_obj and settings_obj.enabled)


def is_restricted(request):
    """True when this request can open only the picked issues."""
    if request is None:
        return False
    restricted = getattr(request, '_trial_restricted', None)
    if restricted is None:
        restricted = is_on(request) and not is_editor(getattr(request, 'user', None))
        request._trial_restricted = restricted
    return restricted


def allowed_issue_ids(request):
    """Ids of the picked issues. Cached on the request."""
    if not hasattr(request, '_trial_issue_ids'):
        settings_obj = get_settings(request)
        request._trial_issue_ids = (
            set(settings_obj.allowed_issues.values_list('pk', flat=True))
            if settings_obj is not None and settings_obj.pk else set()
        )
    return request._trial_issue_ids


# ── Pages ────────────────────────────────────────────────────────────────

def allows_page(request, page):
    """May a restricted request open this Wagtail page?"""
    from articles.models import Article
    from issue.models import Issue

    ids = allowed_issue_ids(request)
    if isinstance(page, Issue):
        return page.pk in ids
    if isinstance(page, Article):
        return (
            page.main_issue_id is None
            or page.main_issue_id in ids
            or page.reprint_appearances.filter(issue_id__in=ids).exists()
        )
    return True


def is_gated_page(page):
    """Only issues and articles can be closed; other pages skip the settings lookup."""
    from articles.models import Article
    from issue.models import Issue

    return isinstance(page, (Issue, Article))


# ── Archive scans ────────────────────────────────────────────────────────

def closed_scans(request):
    """
    The ArchiveIssue scans a restricted request may not open: those whose
    month has a published issue page that isn't picked (the same match as
    Issue.archive_issue).  Cached on the request.
    """
    if not hasattr(request, '_trial_closed_scans'):
        from issue.models import Issue
        from the_librarian.models import ArchiveIssue

        ids = allowed_issue_ids(request)
        open_months = {
            (day.year, day.month)
            for day in Issue.objects.filter(pk__in=ids)
            .exclude(date_of_publishing=None)
            .values_list('date_of_publishing', flat=True)
        }
        closed_months = {
            (day.year, day.month)
            for day in Issue.objects.live().exclude(pk__in=ids)
            .exclude(date_of_publishing=None)
            .values_list('date_of_publishing', flat=True)
        } - open_months
        query = Q()
        for year, month in closed_months:
            query |= Q(year=year, month=month)
        request._trial_closed_scans = (
            list(ArchiveIssue.objects.filter(query).only('pdf_file', 'archive_document'))
            if closed_months else []
        )
    return request._trial_closed_scans


def allows_view(request, view_kwargs):
    """May a restricted request open this the_librarian view?"""
    url_name = request.resolver_match.url_name
    if url_name in SCAN_VIEWS:
        return view_kwargs.get('issue_id') not in {s.pk for s in closed_scans(request)}
    if url_name in DOCUMENT_VIEWS:
        # A search result opens a scan through its ArchiveDocument.
        closed = {s.archive_document_id for s in closed_scans(request)}
        return view_kwargs.get('document_id') not in closed
    return True


def allows_archive_file(request, name):
    """
    May a restricted request fetch MEDIA_ROOT/`name` (e.g. 'archive/x.pdf')
    through Caddy?  Editors and trial mode off: always.
    """
    if not is_restricted(request):
        return True
    return name not in {s.pdf_file.name for s in closed_scans(request) if s.pdf_file}


class TrialModeMiddleware:
    """
    The gate for the archive's Django views. Issue and article pages are
    closed by the before_serve_page hook, which knows which page a URL is.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        match = request.resolver_match
        if match is None or match.namespace != 'the_librarian':
            return None
        if match.url_name not in SCAN_VIEWS | DOCUMENT_VIEWS:
            return None
        if not is_restricted(request) or allows_view(request, view_kwargs):
            return None
        return unavailable(request)


# ── The "not available" page ─────────────────────────────────────────────

def unavailable(request):
    """The 404 a restricted visitor gets for something closed, listing the picked issues."""
    from home.theme import themed_template
    from issue.models import Issue

    settings_obj = get_settings(request)
    issues = (
        Issue.objects.live()
        .filter(pk__in=allowed_issue_ids(request))
        .select_related('topic', 'cover_image', 'volume')
        .order_by('-date_of_publishing')
    )
    return render(
        request,
        themed_template(request, 'trial_unavailable.html'),
        {
            'heading': (settings_obj.unavailable_heading or '').strip() or _('ഇപ്പോൾ ലഭ്യമല്ല'),
            'message': (settings_obj.unavailable_message or '').strip() or _(
                'പരീക്ഷണ ഘട്ടത്തിൽ ഈ ഉള്ളടക്കം ലഭ്യമല്ല. ഇപ്പോൾ വായിക്കാവുന്ന ലക്കങ്ങൾ താഴെ.'
            ),
            'issues': issues,
        },
        status=404,
    )


# ── The banner ───────────────────────────────────────────────────────────

def banner(request):
    """What includes/trial_banner.html shows: {} when trial mode is off."""
    if request is None or not is_on(request):
        return {}
    settings_obj = get_settings(request)
    info = {
        'message': (settings_obj.banner_message or '').strip(),
        'editor': is_editor(getattr(request, 'user', None)),
    }
    if info['editor']:
        info['issue_count'] = len(allowed_issue_ids(request))
        policy = settings_obj.get_permission_policy()
        if policy.user_has_permission_for_instance(request.user, 'change', settings_obj.site):
            try:
                info['settings_url'] = reverse(
                    'wagtailsettings:edit',
                    args=['home', 'trialmodesettings', settings_obj.site_id],
                )
            except NoReverseMatch:
                pass
    return info
