import json
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
        self.user.activate_subscription("1_month")  # the viewer is paywalled
        request.user = self.user

        from the_librarian.views import viewer_view
        with self.assertRaises(Http404):
            viewer_view(request, document_id=999)

    def test_viewer_view_renders_successfully(self):
        """viewer_view renders successfully for valid documents."""
        request = self.factory.get(f"/librarian/viewer/{self.doc.id}/")
        self.user.activate_subscription("1_month")  # the viewer is paywalled
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

    @patch('django_q.tasks.async_task', return_value='task-123')
    def test_trigger_ingestion_success_for_superuser(self, mock_async):
        """Superusers can queue ingestion of one ArchiveIssue."""
        request = self.factory.post("/librarian/ingest/", {"archive_issue_pk": "42", "force": "true"})
        request.user = self.admin  # Superuser

        from the_librarian.views import trigger_ingestion
        response = trigger_ingestion(request)
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response, JsonResponse)
        self.assertEqual(json.loads(response.content), {"success": True, "task_id": "task-123"})
        mock_async.assert_called_once_with(
            'the_librarian.tasks.async_ingest_archive_issue', 42, True,
        )

    def test_trigger_ingestion_rejects_missing_or_bad_pk(self):
        """A missing or non-integer archive_issue_pk is a 400, never a 500."""
        from the_librarian.views import trigger_ingestion
        for data in ({}, {"archive_issue_pk": "abc"}):
            request = self.factory.post("/librarian/ingest/", data)
            request.user = self.admin
            response = trigger_ingestion(request)
            self.assertEqual(response.status_code, 400)

    @patch('the_librarian.services.ingestion.request_stop')
    def test_stop_ingestion_success_for_superuser(self, mock_stop):
        """Superusers can request an ingestion shutdown/stop."""
        request = self.factory.post("/librarian/stop-ingest/")
        request.user = self.admin

        from the_librarian.views import stop_ingestion
        response = stop_ingestion(request)
        self.assertEqual(response.status_code, 200)
        mock_stop.assert_called_once()


class ArchiveAccessTests(TestCase):
    """
    Every librarian route that serves archive content (PDFs, page images,
    page counts, viewers) follows the same rule as Caddy's forward_auth:
    subscribers, staff and superusers only. They used to need only a login.
    """

    ROUTES = [
        ("the_librarian:viewer", [999]),
        ("the_librarian:serve_pdf", [999]),
        ("the_librarian:document_page_meta", [999]),
        ("the_librarian:document_page_image", [999, 1]),
        ("the_librarian:magazine_viewer", [999]),
        ("the_librarian:serve_magazine_pdf", [999]),
        ("the_librarian:download_magazine_pdf", [999]),
        ("the_librarian:magazine_page_meta", [999]),
        ("the_librarian:magazine_page_image", [999, 1]),
        ("the_librarian:archive_viewer", ["missing.pdf"]),
        ("the_librarian:archive_download", ["missing.pdf"]),
        ("the_librarian:archive_page_meta", ["missing.pdf"]),
        ("the_librarian:archive_page_image", ["missing.pdf", 1]),
    ]

    def setUp(self):
        self.reader = User.objects.create_user(phone_number="+918800220001", name="Free Reader")
        self.subscriber = User.objects.create_user(phone_number="+918800220002", name="Subscriber")
        self.subscriber.activate_subscription("1_month")
        self.staff = User.objects.create_user(phone_number="+918800220003", name="Staff", is_staff=True)

    def _get_all(self):
        return [(name, self.client.get(reverse(name, args=args))) for name, args in self.ROUTES]

    def test_anonymous_visitors_are_sent_to_login(self):
        for name, response in self._get_all():
            with self.subTest(route=name):
                self.assertEqual(response.status_code, 302)
                self.assertIn("/accounts/login/", response.url)

    def test_logged_in_non_subscribers_are_refused(self):
        self.client.force_login(self.reader)
        for name, response in self._get_all():
            with self.subTest(route=name):
                # Refused before the lookup, so it doesn't reveal what exists.
                self.assertEqual(response.status_code, 403)

    def test_subscribers_and_staff_get_through(self):
        for user in (self.subscriber, self.staff):
            self.client.force_login(user)
            for name, response in self._get_all():
                with self.subTest(user=user.name, route=name):
                    # Past the gate: these ids/filenames don't exist (404),
                    # or the viewer shell renders without touching the file.
                    self.assertIn(response.status_code, (200, 404))

    def test_viewer_page_param_is_validated(self):
        doc = ArchiveDocument.objects.create(filename="a.pdf", file_path="a.pdf", total_pages=1)
        self.client.force_login(self.subscriber)
        response = self.client.get(reverse("the_librarian:viewer", args=[doc.pk]), {"page": "abc"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page"], 1)

    def test_page_images_are_not_publicly_cacheable(self):
        import tempfile
        from pathlib import Path

        from the_librarian import views

        with tempfile.NamedTemporaryFile(suffix=".webp") as f:
            with patch.object(views.page_cache, "get_page_image_path", return_value=f.name):
                response = views._serve_page_image(Path("x.pdf"), 1)
            self.assertEqual(response["Cache-Control"], "private, max-age=86400")
            # Not response.close(): that fires request_finished, which closes
            # the test's DB connection.
            response.file_to_stream.close()

    def test_caddy_forward_auth_uses_the_same_rule(self):
        from Icarus.access import may_read_archive

        self.assertFalse(may_read_archive(self.reader))
        self.assertTrue(may_read_archive(self.subscriber))
        self.assertTrue(may_read_archive(self.staff))
        for user, expected in ((self.reader, 403), (self.subscriber, 400)):
            self.client.force_login(user)
            # 400 = authorised, but no X-Forwarded-Uri (Caddy always sends one)
            self.assertEqual(self.client.get("/internal/archive-authz").status_code, expected)
            self.assertEqual(self.client.get("/internal/page-cache-authz").status_code, expected)


class SearchRedactionTests(TestCase):
    """Search is public, but paywalled PDF text is only shown in full to archive readers."""

    LONG_TEXT = "പാഠം " * 200  # ~1200 characters

    def setUp(self):
        self.result = {
            "chunk_id": 1, "type": "pdf", "title": "Issue", "document_id": 1,
            "page_number": 2, "file_path": "/srv/archive/secret/path.pdf",
            "chunk_text": self.LONG_TEXT, "score": 0.9,
        }
        self.subscriber = User.objects.create_user(phone_number="+918800220004", name="Subscriber")
        self.subscriber.activate_subscription("1_month")

    def _api(self, **params):
        with patch("the_librarian.views.search_hybrid", return_value=[dict(self.result)]), \
             patch("the_librarian.views.search_by_document", return_value=[dict(self.result)]):
            return self.client.get(reverse("the_librarian:search_api"), {"q": "x", **params})

    def test_non_subscribers_get_a_preview(self):
        data = self._api().json()["results"][0]
        self.assertLessEqual(len(data["chunk_text"]), 201)
        self.assertTrue(data["preview_only"])
        self.assertNotIn("file_path", data)

    def test_subscribers_get_full_text_but_never_the_path(self):
        self.client.force_login(self.subscriber)
        data = self._api().json()["results"][0]
        self.assertEqual(data["chunk_text"], self.LONG_TEXT)
        self.assertNotIn("preview_only", data)
        self.assertNotIn("file_path", data)

    def test_searching_inside_one_pdf_requires_archive_access(self):
        self.assertEqual(self._api(document="issue.pdf").status_code, 403)
        self.client.force_login(self.subscriber)
        self.assertEqual(self._api(document="issue.pdf").status_code, 200)

    def test_search_page_is_redacted_too(self):
        with patch("the_librarian.views.search_hybrid", return_value=[dict(self.result)]):
            response = self.client.get(reverse("the_librarian:search"), {"q": "x"})
        self.assertNotContains(response, self.LONG_TEXT)
        self.assertNotContains(response, "/srv/archive/secret/path.pdf")


class TaskStatusAccessTests(TestCase):
    def test_superusers_only(self):
        url = reverse("the_librarian:task_status", args=["abc"])
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(User.objects.create_user(phone_number="+918800220005", name="R"))
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(User.objects.create_superuser(phone_number="+918800220006", name="A", password="x"))
        self.assertEqual(self.client.get(url).status_code, 200)


class EmbeddingDimensionCheckTests(TestCase):
    """the_librarian.W001: EMBEDDING_DIM must match the migrated vector(N) column."""

    def test_matching_dimension_is_clean(self):
        from django.test import override_settings

        from the_librarian.checks import embedding_dimension_matches_schema

        with override_settings(LIBRARIAN_EMBEDDING_DIM=1024):
            self.assertEqual(embedding_dimension_matches_schema(None), [])

    def test_mismatch_is_a_warning_not_an_error(self):
        from django.core.checks import Warning
        from django.test import override_settings

        from the_librarian.checks import embedding_dimension_matches_schema

        with override_settings(LIBRARIAN_EMBEDDING_DIM=768):
            issues = embedding_dimension_matches_schema(None)
        self.assertEqual([i.id for i in issues], ["the_librarian.W001"])
        self.assertIsInstance(issues[0], Warning)  # reported, but never blocks boot
        self.assertIn("vector(1024)", issues[0].msg)


class NearestNeighbourSearchTests(TestCase):
    """
    HNSW returns at most hnsw.ef_search (default 40) candidates before the
    WHERE filters run, so a filtered search could come back short or empty.
    """

    DIM = 1024

    def _vec(self, hot_index, noise=0.0):
        v = [0.0] * self.DIM
        v[hot_index] = 1.0
        v[(hot_index + 1) % self.DIM] = noise
        return v

    def setUp(self):
        from datetime import date

        from articles.models import Article

        root = Page.get_first_root_node()
        hidden = Article(title="Draft", slug="draft", date=date(2026, 1, 1), live=False)
        root.add_child(instance=hidden)
        doc = ArchiveDocument.objects.create(filename="a.pdf", file_path="a.pdf", total_pages=1)

        # 60 chunks right next to the query, all filtered out (draft article)...
        DocumentChunk.objects.bulk_create([
            DocumentChunk(article=hidden, chunk_index=i, chunk_text=f"draft {i}",
                          embedding=self._vec(0, noise=0.001 * (i + 1)))
            for i in range(60)
        ])
        # ...and 3 genuine but more distant matches.
        DocumentChunk.objects.bulk_create([
            DocumentChunk(document=doc, chunk_index=i, page_number=1, chunk_text=f"real {i}",
                          embedding=self._vec(0, noise=0.5 + 0.1 * i))
            for i in range(3)
        ])

    def test_filtered_results_still_fill_top_k(self):
        from django.db import connection

        from the_librarian.services import search

        with connection.cursor() as cursor:
            # Force the HNSW index (a tiny test table would otherwise seq-scan).
            cursor.execute("SET enable_seqscan = off")
        try:
            with patch.object(search, "embed_query", return_value=self._vec(0)):
                results = search.search_similar("q", top_k=3)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("RESET enable_seqscan")

        self.assertEqual([r["chunk_text"] for r in results], ["real 0", "real 1", "real 2"])


class ChunkUpsertTests(TestCase):
    def test_resubmitted_batch_updates_in_place_without_auditing(self):
        from auditlog import get_logentry_model
        from django.contrib.contenttypes.models import ContentType

        from the_librarian.models import ArchiveIssue
        from the_librarian.services.ingestion import persist_ingested_batch
        from the_librarian.services.vector_schema import VectorRecord

        issue = ArchiveIssue.objects.create(year=2020, month=5, volume=1, issue_number=1)
        vec = [0.1] * 1024

        def batch(text):
            return [VectorRecord(chunk_text=f"{text} {i}", page_number=1, chunk_index=i, embedding=vec) for i in range(3)]

        doc = persist_ingested_batch(archive_issue=issue, batch_index=0, is_final=False, total_pages=1, records=batch("first"))
        persist_ingested_batch(archive_issue=issue, batch_index=1, is_final=True, total_pages=1, records=batch("second"))

        chunks = DocumentChunk.objects.filter(document=doc).order_by("chunk_index")
        self.assertEqual([c.chunk_text for c in chunks], ["second 0", "second 1", "second 2"])
        self.assertFalse(get_logentry_model().objects.filter(
            content_type=ContentType.objects.get_for_model(DocumentChunk)
        ).exists())


class IngestPanelCountTests(TestCase):
    def test_counts_articles_not_chunks(self):
        from datetime import date

        from articles.models import Article
        from the_librarian.wagtail_hooks import LibrarianIngestPanel

        root = Page.get_first_root_node()
        art = Article(title="Indexed", slug="indexed", date=date(2026, 1, 1))
        root.add_child(instance=art)
        DocumentChunk.objects.bulk_create([
            DocumentChunk(article=art, chunk_index=i, chunk_text="x", embedding=[0.1] * 1024) for i in range(4)
        ])

        html = LibrarianIngestPanel().render_html()
        self.assertIn('id="stat-chunks">4<', html)
        # One article, however many chunks. (count() clears DocumentChunk's
        # default ordering, so the DISTINCT is over article ids alone.)
        self.assertIn('id="stat-articles">1<', html)
