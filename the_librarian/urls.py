from django.urls import path
from the_librarian import views

app_name = "the_librarian"

urlpatterns = [
    # ── Public archive & search ──────────────────────────────────────────
    path("archive/",    views.archive_list, name="archive_list"),
    path("search/",     views.search_view,  name="search"),
    path("api/search/", views.search_api,   name="search_api"),

    # ── ArchiveIssue: viewer / inline PDF / force-download ─────────────
    path("archive/<int:issue_id>/",                     views.magazine_viewer,       name="magazine_viewer"),
    path("archive/<int:issue_id>/Sasthragathi.pdf",     views.serve_magazine_pdf,    name="serve_magazine_pdf"),
    path("archive/<int:issue_id>/download/",            views.download_magazine_pdf, name="download_magazine_pdf"),
    path("archive/<int:issue_id>/pages/meta.json",              views.magazine_page_meta,  name="magazine_page_meta"),
    path("archive/<int:issue_id>/pages/<int:page_number>.webp", views.magazine_page_image, name="magazine_page_image"),

    # ── ArchiveDocument ViewerJS display (search results → viewer) ───────
    path("viewer/<int:document_id>/", views.viewer_view, name="viewer"),
    path("pdf/<int:document_id>/",    views.serve_pdf,   name="serve_pdf"),
    path("viewer/<int:document_id>/pages/meta.json",              views.document_page_meta,  name="document_page_meta"),
    path("viewer/<int:document_id>/pages/<int:page_number>.webp", views.document_page_image, name="document_page_image"),

    # ── Raw filesystem PDF access (ingestion dashboard) ──────────────────
    path("archive/view/<str:filename>/",     views.archive_viewer,   name="archive_viewer"),
    path("archive/download/<str:filename>/", views.archive_download, name="archive_download"),
    path("archive/view/<str:filename>/pages/meta.json",              views.archive_page_meta,  name="archive_page_meta"),
    path("archive/view/<str:filename>/pages/<int:page_number>.webp", views.archive_page_image, name="archive_page_image"),

    # ── Admin ingestion API (local, in-process pipeline) ─────────────────
    path("api/ingest/",                        views.trigger_ingestion,    name="trigger_ingestion"),
    path("api/stop/",                          views.stop_ingestion,       name="stop_ingestion"),
    path("api/pending/",                       views.get_pending_pdfs_view, name="get_pending_pdfs"),
    path("api/task-status/<str:task_id>/",     views.task_status_view,     name="task_status"),

    # ── Remote ingestion worker (off-box OCR) ────────────────────────────
    path("api/worker/pending/",                 views.list_pending_for_worker,  name="worker_pending"),
    path("api/worker/download/<int:issue_id>/", views.worker_download_pdf,     name="worker_download_pdf"),
    path("api/worker/submit/",                  views.submit_ingested_chunks,  name="worker_submit"),
    path("api/worker/toggle/",                  views.toggle_remote_ingestion, name="toggle_remote_ingestion"),
    path("api/worker/status/",                  views.remote_ingestion_status, name="remote_ingestion_status"),

    # ── Remote ingestion API keys ─────────────────────────────────────────
    path("api/worker/keys/",                     views.list_api_keys,   name="list_api_keys"),
    path("api/worker/keys/create/",              views.create_api_key,  name="create_api_key"),
    path("api/worker/keys/<int:key_id>/revoke/", views.revoke_api_key,  name="revoke_api_key"),

    # ── Remote worker admin page (linked from the sidebar menu) ──────────
    path("remote-worker/", views.remote_worker_admin, name="remote_worker_admin"),
]