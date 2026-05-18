from unittest.mock import patch, MagicMock

from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.http import JsonResponse, Http404
from django.urls import reverse
from django.conf import settings
from wagtail.models import Page, Site

from the_librarian.models import ArchiveDocument, DocumentChunk
from home.models import HomePage

User = get_user_model()


class TheLibrarianModelTests(TestCase):
    """
    Tests for ArchiveDocument and DocumentChunk models.
    """

    def test_archive_document_str(self):
        doc = ArchiveDocument(filename="science_jan_2026.pdf", file_path="2026/jan.pdf", total_pages=12)
        self.assertEqual(str(doc), "science_jan_2026.pdf")

    def test_document_chunk_str_pdf_source(self):
        doc = ArchiveDocument(id=1, filename="science_jan_2026.pdf", file_path="2026/jan.pdf", total_pages=12)
        chunk = DocumentChunk(
            document=doc,
            page_number=3,
            chunk_index=1,
            chunk_text="Malayalam text content"
        )
        self.assertEqual(str(chunk), "science_jan_2026.pdf — p.3 chunk#1")

    def test_document_chunk_str_unknown_source(self):
        chunk = DocumentChunk(
            chunk_index=5,
            chunk_text="No document source"
        )
        self.assertEqual(str(chunk), "Unknown Source — chunk#5")


class LibrarianViewsAndPermissionTests(TestCase):
    """
    Integration tests for searching and ingestion API views.
    """

    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(
            phone_number="+918800110011",
            name="Regular Reader",
            password="readerpassword"
        )
        self.admin = User.objects.create_superuser(
            phone_number="+919900110011",
            name="Admin Staff",
            password="adminpassword"
        )
        # Setup mock archive document
        self.doc = ArchiveDocument.objects.create(
            filename="science_2026.pdf",
            file_path="2026/science_2026.pdf",
            total_pages=5
        )

    @patch('the_librarian.views.search_hybrid')
    def test_search_view_renders_template_with_query(self, mock_search_hybrid):
        """Test search page view with query terms and search execution."""
        mock_search_hybrid.return_value = [{
            "chunk_id": 1,
            "title": "Result 1",
            "score": 0.85,
            "chunk_text": "text",
            "type": "pdf",
            "document_name": "science_2026.pdf",
            "document_id": 1,
            "page_number": 3,
            "search_type": "hybrid",
            "language": "ml"
        }]

        request = self.factory.get("/librarian/search/?q=science&mode=hybrid")
        request.user = self.user

        from the_librarian.views import search_view
        response = search_view(request)
        self.assertEqual(response.status_code, 200)
        # Search service must have been triggered
        mock_search_hybrid.assert_called_once_with("science", top_k=5)

    @patch('the_librarian.views.search_similar')
    def test_search_api_returns_json(self, mock_search_similar):
        """AJAX JSON search endpoints return correct structures."""
        mock_search_similar.return_value = [{
            "chunk_id": 1,
            "title": "Result 1",
            "score": 0.72,
            "chunk_text": "text",
            "type": "pdf",
            "document_name": "nature.pdf",
            "document_id": 2,
            "page_number": 1,
            "search_type": "similarity",
            "language": "ml"
        }]

        request = self.factory.get("/librarian/search/api/?q=nature&mode=similarity")
        request.user = self.user

        from the_librarian.views import search_api
        response = search_api(request)
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response, JsonResponse)
        mock_search_similar.assert_called_once_with("nature", top_k=5)

    def test_viewer_view_returns_404_for_invalid_doc(self):
        """viewer_view returns 404 for invalid document IDs."""
        request = self.factory.get("/librarian/viewer/999/")
        request.user = self.user

        from the_librarian.views import viewer_view
        with self.assertRaises(Http404):
            viewer_view(request, document_id=999)

    def test_viewer_view_renders_successfully(self):
        """viewer_view renders successfully for valid documents."""
        request = self.factory.get(f"/librarian/viewer/{self.doc.id}/")
        request.user = self.user

        from the_librarian.views import viewer_view
        response = viewer_view(request, document_id=self.doc.id)
        self.assertEqual(response.status_code, 200)

    def test_trigger_ingestion_blocks_regular_users(self):
        """Ingestion trigger must reject non-superuser requests with a 403 status."""
        request = self.factory.post("/librarian/ingest/")
        request.user = self.user  # Regular user

        from the_librarian.views import trigger_ingestion
        response = trigger_ingestion(request)
        self.assertEqual(response.status_code, 403)
        self.assertIn("Only administrators can perform this action.", response.content.decode('utf-8'))

    @patch('the_librarian.services.ingestion.ingest_archive')
    def test_trigger_ingestion_success_for_superuser(self, mock_ingest):
        """Superusers can successfully trigger the archive ingestion pipeline."""
        mock_ingest.return_value = {"processed": ["doc1.pdf"], "skipped": [], "errors": []}

        request = self.factory.post("/librarian/ingest/", {"force": "true"})
        request.user = self.admin  # Superuser

        from the_librarian.views import trigger_ingestion
        response = trigger_ingestion(request)
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response, JsonResponse)
        mock_ingest.assert_called_once_with(force=True, filename=None)

    @patch('the_librarian.services.ingestion.request_stop')
    def test_stop_ingestion_success_for_superuser(self, mock_stop):
        """Superusers can request an ingestion shutdown/stop."""
        request = self.factory.post("/librarian/stop-ingest/")
        request.user = self.admin

        from the_librarian.views import stop_ingestion
        response = stop_ingestion(request)
        self.assertEqual(response.status_code, 200)
        mock_stop.assert_called_once()
