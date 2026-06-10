from django.urls import path
from the_librarian import views

app_name = "the_librarian"

urlpatterns = [
    # ── Public archive & search ──────────────────────────────────────────
    # archive/ matches the original path so existing bookmarks still work
    path("archive/",    views.archive_list, name="archive_list"),
    path("search/",     views.search_view,  name="search"),
    path("api/search/", views.search_api,   name="search_api"),

    # ── ArchiveIssue: viewer / inline PDF / force-download ─────────────
    path("archive/<int:issue_id>/", views.magazine_viewer, name="magazine_viewer",),
    path("archive/<int:issue_id>/Sasthragathi.pdf", views.serve_magazine_pdf, name="serve_magazine_pdf",),
    path("archive/<int:issue_id>/download/", views.download_magazine_pdf, name="download_magazine_pdf",),

    # ── ArchiveDocument ViewerJS display (search results → viewer) ───────
    path("viewer/<int:document_id>/", views.viewer_view, name="viewer",),
    path("pdf/<int:document_id>/", views.serve_pdf, name="serve_pdf",),

    # ── Raw filesystem PDF access (used by the ingestion dashboard) ──────
    path("archive/view/<str:filename>/", views.archive_viewer, name="archive_viewer",),
    path("archive/download/<str:filename>/", views.archive_download, name="archive_download",),

    # ── Admin ingestion API — paths & names match the original exactly ────
    # wagtail_hooks.py resolves these by name via reverse(); keep names stable
    path("api/ingest/", views.trigger_ingestion, name="trigger_ingestion"),
    path("api/stop/", views.stop_ingestion, name="stop_ingestion"),
    path("api/pending/", views.get_pending_pdfs_view, name="get_pending_pdfs"),
]