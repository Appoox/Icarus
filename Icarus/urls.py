from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.urls import include, path
from django.contrib import admin
from django.views.decorators.cache import never_cache
from django.views.generic import TemplateView

from wagtail.admin import urls as wagtailadmin_urls
from wagtail import urls as wagtail_urls
from wagtail.documents import urls as wagtaildocs_urls
from wagtail.contrib.sitemaps.views import sitemap
from Icarus import protected_media as protected_media_views
from Icarus import views as core_views
from reader import views as reader_views

# ── Never language-prefixed: infra, admin, machine endpoints ──────────
urlpatterns = [
    path("sitemap.xml", sitemap),
    path(
        "robots.txt",
        TemplateView.as_view(
            template_name="robots.txt", content_type="text/plain"
        ),
    ),
    # Removes a leftover service worker from browsers that still have one
    # for this address (see the template).  Never cached, so a browser
    # always gets the current file.
    path(
        "sw.js",
        never_cache(
            TemplateView.as_view(
                template_name="sw.js", content_type="text/javascript"
            )
        ),
    ),
    path("django-admin/", admin.site.urls),
    path("admin/", include(wagtailadmin_urls)),
    path("documents/", include(wagtaildocs_urls)),
    path("internal/archive-authz", protected_media_views.archive_authz, name="archive_authz"),
    path("internal/postbox-authz", protected_media_views.postbox_authz, name="postbox_authz"),
    path("internal/page-cache-authz", protected_media_views.page_cache_authz, name="page_cache_authz"),
]

if settings.DEBUG:
    from django.conf.urls.static import static
    from django.contrib.staticfiles.urls import staticfiles_urlpatterns

    urlpatterns += staticfiles_urlpatterns()
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# ── Language-prefixed public site (ml unprefixed, en at /en/) ─────────
urlpatterns += i18n_patterns(
    # Redirects to guardian page if under 18 (see CustomSignupForm in forms.py).
    # This is the full-screen explainer shown when a minor tries to sign up.
    path("accounts/signup/", reader_views.ReaderSignupView.as_view(), name="account_signup"),
    path("accounts/guardian/", reader_views.guardian_account_info, name="guardian_account_info"),
    path("accounts/", include("allauth.urls")),
    path("reader/", include("reader.urls")),
    path("issues/", include("issue.urls")),
    path("articles/", include("articles.urls")),
    path("librarian/", include("the_librarian.urls")),
    path("kalapila/", include("kalapila.urls")),
    path("postbox/", include("postbox.urls")),
    path("authors/", include("literati.urls")),
    path("under-construction/", core_views.under_construction, name="under_construction"),
    path("", include(wagtail_urls)),  # must stay last
    prefix_default_language=False,
)

# Error pages follow the site design chosen in Settings → Site theme.
handler403 = "Icarus.views.permission_denied"
handler404 = "Icarus.views.page_not_found"
handler500 = "Icarus.views.server_error"
