from django.urls import path
from the_librarian import views

app_name = "the_librarian"

urlpatterns = [
    # Search
    path("search/", views.search_view, name="search"),
    path("api/search/", views.search_api, name="search_api"),

    # ViewerJS PDF display
    path("viewer/<int:document_id>/", views.viewer_view, name="viewer"),
    path("pdf/<int:document_id>/", views.serve_pdf, name="serve_pdf"),

    # Admin ingestion trigger
    path("api/ingest/", views.trigger_ingestion, name="trigger_ingestion"),
]
