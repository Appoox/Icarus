from unittest.mock import patch

from django.test import TestCase, RequestFactory
from django.template.response import TemplateResponse
from wagtail.models import Page

from search.views import search
from home.models import HomePage


class SearchViewsTests(TestCase):
    """
    Tests for the site-wide Wagtail search view.
    """

    def setUp(self):
        self.factory = RequestFactory()
        self.root = Page.get_first_root_node()
        self.home = HomePage(title="Hello Home Page", slug="hello-home")
        self.root.add_child(instance=self.home)

    def test_search_view_empty_query(self):
        """Empty queries should return empty results in search view context."""
        request = self.factory.get("/search/?query=")
        response = search(request)

        self.assertIsInstance(response, TemplateResponse)
        self.assertEqual(response.template_name, "search/search.html")
        self.assertIn("search_query", response.context_data)
        self.assertIn("search_results", response.context_data)
        self.assertEqual(response.context_data["search_query"], "")
        self.assertEqual(len(response.context_data["search_results"]), 0)

    @patch('wagtail.query.PageQuerySet.search')
    def test_search_view_with_results(self, mock_search):
        """Populated search queries successfully query Wagtail index for live pages."""
        mock_search.return_value = [self.home]

        # Query for 'Hello' which matches the HomePage title
        request = self.factory.get("/search/?query=Hello")
        response = search(request)

        self.assertIsInstance(response, TemplateResponse)
        self.assertEqual(response.context_data["search_query"], "Hello")
        
        # Wagtail database search matching Hello Home Page
        results = response.context_data["search_results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Hello Home Page")

    @patch('wagtail.query.PageQuerySet.search')
    def test_search_view_invalid_page_number_pagination_fallback(self, mock_search):
        """Invalid page queries fallback elegantly to page 1."""
        mock_search.return_value = [self.home]

        # Query with non-integer page number
        request = self.factory.get("/search/?query=Hello&page=invalid")
        response = search(request)

        self.assertIsInstance(response, TemplateResponse)
        results = response.context_data["search_results"]
        self.assertEqual(results.number, 1)

    @patch('wagtail.query.PageQuerySet.search')
    def test_search_view_out_of_range_page_number_pagination_fallback(self, mock_search):
        """Page numbers out of bounds default to the last available page."""
        mock_search.return_value = [self.home]

        request = self.factory.get("/search/?query=Hello&page=999")
        response = search(request)

        self.assertIsInstance(response, TemplateResponse)
        results = response.context_data["search_results"]
        # Since we only have 1 result (1 page), it should default back to page 1
        self.assertEqual(results.number, 1)
