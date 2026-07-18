from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.urls import include, path
from django.contrib import admin

from wagtail.admin import urls as wagtailadmin_urls
from wagtail import urls as wagtail_urls
from wagtail.documents import urls as wagtaildocs_urls
from wagtail.contrib.sitemaps.views import sitemap
from Icarus import protected_media as protected_media_views
from Icarus import views as core_views

# ── Never language-prefixed: infra, admin, machine endpoints ──────────
urlpatterns = [
    path("sitemap.xml", sitemap),
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
    path("accounts/", include("allauth.urls")),
    # path("search/", search_views.search, name="search"),
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