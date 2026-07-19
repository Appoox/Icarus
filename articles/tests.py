from datetime import date
from unittest.mock import patch

from django.test import TestCase, RequestFactory, override_settings
from django.contrib.auth import get_user_model
from django.http import HttpResponseRedirect
from wagtail.models import Page, Site
from wagtail.test.utils import WagtailPageTestCase

from articles.models import Article, ArticleIndexPage, ArticleForm
from articles import crawler_utils
from issue.models import Issue, IssueIndexPage, Volume
from home.models import HomePage

User = get_user_model()
from django.contrib.auth.models import AnonymousUser

class MockSession(dict):
    session_key = "test_session_key"


class ArticleModelAndPaywallTests(WagtailPageTestCase):
    """
    Unit and integration tests for Article models, properties, and paywall context logic.
    """

    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()

        # Clean up existing pages/sites correctly via instance-level deletes to prevent treebeard corruption
        Site.objects.all().delete()
        for p in Page.objects.filter(slug__in=['home', 'articles', 'issues', 'authors']):
            try:
                p.delete()
            except Exception:
                pass

        # Fetch fresh root node AFTER deletions so Treebeard's parent metadata (numchild, path) is fresh
        self.root = Page.get_first_root_node()

        # Ensure a default site is set up
        self.site = Site.objects.filter(is_default_site=True).first()
        if not self.site:
            self.site = Site.objects.create(
                hostname="testserver",
                root_page=self.root,
                is_default_site=True
            )

        # Create HomePage cleanly
        self.home = HomePage(title="Home", slug="home")
        self.root.add_child(instance=self.home)

        # Create ArticleIndexPage
        self.article_index = ArticleIndexPage(title="Articles", slug="articles")
        self.home.add_child(instance=self.article_index)

        # Create IssueIndexPage and Volume for ForeignKey setups
        self.issue_index = IssueIndexPage(title="Issues", slug="issues")
        self.home.add_child(instance=self.issue_index)

        self.volume = Volume.objects.create(
            number=1,
            year_start=2026
        )

        self.issue = Issue(
            title="January 2026",
            slug="jan-2026",
            volume=self.volume,
            issue_number=1,
            date_of_publishing=date(2026, 1, 1)
        )
        self.issue_index.add_child(instance=self.issue)


        # Create Article
        self.article = Article(
            title="Test Article",
            slug="test-article",
            date=date(2026, 1, 1),
            main_issue=self.issue,
            cover_image_aspect="2x1",
            body=[
                ('paragraph', '<p>First block of text</p>'),
                ('paragraph', '<p>Second block of text</p>'),
                ('paragraph', '<p>Third block of text</p>'),
            ]
        )
        self.article_index.add_child(instance=self.article)

        # Ensure correct class definitions
        self.article = Article.objects.get(pk=self.article.pk)

    def test_article_parent_page_types(self):
        """Verify that Articles can only be created under ArticleIndexPage."""
        self.assertAllowedParentPageTypes(Article, {ArticleIndexPage})

    def test_article_subpage_types(self):
        """Verify that Articles do not allow subpages."""
        self.assertAllowedSubpageTypes(Article, {})

    def test_article_index_page_subpage_types(self):
        """Verify that ArticleIndexPage only allows Articles as subpages."""
        self.assertAllowedSubpageTypes(ArticleIndexPage, {Article})

    def test_cover_fill_spec(self):
        """Test that cover_fill_spec helper property translates aspect ratio choices correctly."""
        self.article.cover_image_aspect = "3x1"
        self.assertEqual(self.article.cover_fill_spec, "fill-1200x400")

        self.article.cover_image_aspect = "1x1"
        self.assertEqual(self.article.cover_fill_spec, "fill-1200x1200")

        # Default fallback test
        self.article.cover_image_aspect = "invalid"
        self.assertEqual(self.article.cover_fill_spec, "fill-1200x600")

    def test_excerpt(self):
        """Test excerpt helper property extracts first paragraph block."""
        self.assertEqual(str(self.article.excerpt), "<p>First block of text</p>")

        # Test empty body
        empty_article = Article(title="Empty", slug="empty", date=date(2026, 1, 1))
        self.assertEqual(empty_article.excerpt, "")

    def test_article_index_page_context(self):
        """Test ArticleIndexPage.get_context fetches live child articles."""
        request = self.factory.get("/articles/")
        context = self.article_index.get_context(request)
        self.assertIn("articles", context)
        self.assertEqual(len(context["articles"]), 1)
        self.assertEqual(context["articles"][0].id, self.article.id)

    def test_paywall_logic_superuser_exemption(self):
        """Superusers and staff must be exempted from the paywall."""
        admin_user = User.objects.create_superuser(
            phone_number="+919999999999",
            name="Admin User",
            password="adminpassword"
        )
        request = self.factory.get(self.article.url)
        request.user = admin_user
        request.session = MockSession()
        
        context = self.article.get_context(request)
        self.assertFalse(context["show_paywall"])

    def test_paywall_logic_subscribed_reader(self):
        """Subscribed users must be exempted and the article recorded in their read history."""
        subscribed_user = User.objects.create_user(
            phone_number="+918888888888",
            name="Subscribed User",
            password="password123"
        )
        # Activate subscription
        subscribed_user.subscription_plan = "1_month"
        from django.utils import timezone
        subscribed_user.subscription_start = timezone.now()
        subscribed_user.subscription_end = timezone.now() + timezone.timedelta(days=30)
        subscribed_user.save()

        request = self.factory.get(self.article.url)
        request.user = subscribed_user
        request.session = MockSession()

        context = self.article.get_context(request)
        self.assertFalse(context["show_paywall"])
        self.assertTrue(subscribed_user.read_articles.filter(pk=self.article.pk).exists())

    def test_paywall_logic_unsubscribed_reader_under_limit(self):
        """Unsubscribed readers are allowed to read free articles up to the limit."""
        unsubscribed_user = User.objects.create_user(
            phone_number="+917777777777",
            name="Unsubscribed User",
            password="password123"
        )
        
        request = self.factory.get(self.article.url)
        request.user = unsubscribed_user
        request.session = MockSession()

        context = self.article.get_context(request)
        self.assertFalse(context["show_paywall"])
        self.assertTrue(unsubscribed_user.read_articles.filter(pk=self.article.pk).exists())

    def test_paywall_logic_unsubscribed_reader_over_limit(self):
        """Unsubscribed readers over the FREE_ARTICLE_LIMIT are gated by a paywall."""
        unsubscribed_user = User.objects.create_user(
            phone_number="+916666666666",
            name="Gated User",
            password="password123"
        )

        # Create dummy articles to exceed the limit
        for i in range(5):
            art = Article(
                title=f"Dummy {i}",
                slug=f"dummy-{i}",
                date=date(2026, 1, 1)
            )
            self.article_index.add_child(instance=art)
            unsubscribed_user.read_articles.add(art)

        request = self.factory.get(self.article.url)
        request.user = unsubscribed_user
        request.session = MockSession()

        context = self.article.get_context(request)
        self.assertTrue(context["show_paywall"])
        self.assertIn("truncated_body", context)
        self.assertEqual(len(context["truncated_body"]), 2)  # Limited to first two blocks

    def test_paywall_logic_anonymous_user_limits(self):
        """Anonymous readers are tracked via session and paywalled after the limit."""
        request = self.factory.get(self.article.url)
        # Mock session dictionary
        request.session = MockSession()
        request.user = AnonymousUser()

        # First read
        context = self.article.get_context(request)
        self.assertFalse(context["show_paywall"])
        self.assertIn(self.article.pk, request.session["free_reads"])

        # Exceed limit in session
        request.session["free_reads"] = [101, 102, 103]  # already read 3 articles
        context = self.article.get_context(request)
        self.assertTrue(context["show_paywall"])

    # ── Verified-crawler flexible sampling ─────────────────────────────
    def _crawler_request(self, ua="Googlebot/2.1 (+http://www.google.com/bot.html)", ip="66.249.66.1"):
        request = self.factory.get(self.article.url, HTTP_USER_AGENT=ua)
        request.META["HTTP_X_REAL_IP"] = ip
        request.user = AnonymousUser()
        request.session = MockSession()
        return request

    @override_settings(FREE_ARTICLE_LIMIT=0)
    @patch("articles.crawler_utils.is_verified_crawler", return_value=True)
    def test_verified_crawler_gets_full_body_at_zero_free(self, _mock):
        """A verified crawler bypasses the paywall even at 0 free articles,
        without consuming a metered read or writing the session (no Set-Cookie)."""
        request = self._crawler_request()
        context = self.article.get_context(request)
        self.assertFalse(context["show_paywall"])
        self.assertTrue(context["crawler_full_access"])
        self.assertNotIn("free_reads", request.session)

    @override_settings(FREE_ARTICLE_LIMIT=0)
    @patch("articles.crawler_utils.is_verified_crawler", return_value=False)
    def test_unverified_anonymous_is_paywalled_at_zero_free(self, _mock):
        """An ordinary (unverified) anonymous visitor is paywalled at 0 free."""
        request = self._crawler_request(ua="Mozilla/5.0")
        context = self.article.get_context(request)
        self.assertTrue(context["show_paywall"])
        self.assertFalse(context["crawler_full_access"])

    def test_is_verified_crawler_true_for_genuine_googlebot(self):
        """UA token + PTR ending in .googlebot.com + forward-confirm → verified."""
        crawler_utils._verify_cache.clear()
        request = self._crawler_request(ip="66.249.66.1")
        with patch("articles.crawler_utils.socket.gethostbyaddr",
                   return_value=("crawl-66-249-66-1.googlebot.com", [], ["66.249.66.1"])), \
             patch("articles.crawler_utils.socket.getaddrinfo",
                   return_value=[(2, 1, 6, "", ("66.249.66.1", 0))]):
            self.assertTrue(crawler_utils.is_verified_crawler(request))

    def test_is_verified_crawler_false_for_spoofed_ip(self):
        """Crawler UA from an IP whose PTR is not a Google host → rejected."""
        crawler_utils._verify_cache.clear()
        request = self._crawler_request(ip="1.2.3.4")
        with patch("articles.crawler_utils.socket.gethostbyaddr",
                   return_value=("host.evil.example.com", [], ["1.2.3.4"])):
            self.assertFalse(crawler_utils.is_verified_crawler(request))

    def test_is_verified_crawler_false_when_forward_lookup_mismatches(self):
        """PTR looks like a Bing host but forward DNS resolves elsewhere → rejected."""
        crawler_utils._verify_cache.clear()
        request = self._crawler_request(ua="bingbot/2.0", ip="5.6.7.8")
        with patch("articles.crawler_utils.socket.gethostbyaddr",
                   return_value=("foo.search.msn.com", [], ["5.6.7.8"])), \
             patch("articles.crawler_utils.socket.getaddrinfo",
                   return_value=[(2, 1, 6, "", ("9.9.9.9", 0))]):
            self.assertFalse(crawler_utils.is_verified_crawler(request))

    def test_is_verified_crawler_skips_dns_for_normal_ua(self):
        """A non-crawler User-Agent never triggers a DNS lookup."""
        crawler_utils._verify_cache.clear()
        request = self._crawler_request(ua="Mozilla/5.0", ip="10.0.0.1")
        with patch("articles.crawler_utils.socket.gethostbyaddr") as mock_ptr:
            self.assertFalse(crawler_utils.is_verified_crawler(request))
            mock_ptr.assert_not_called()


class ArticleRoutablePdfTests(TestCase):
    """
    Tests for the Article serve_pdf routable view, ensuring permission gates and generation work.
    """

    def setUp(self):
        self.factory = RequestFactory()
        # Clean up existing pages/sites correctly via instance-level deletes to prevent treebeard corruption
        Site.objects.all().delete()
        for p in Page.objects.filter(slug__in=['home', 'articles', 'issues', 'authors']):
            try:
                p.delete()
            except Exception:
                pass

        # Fetch fresh root node AFTER deletions so Treebeard's parent metadata (numchild, path) is fresh
        self.root = Page.get_first_root_node()

        # Ensure a default site is set up
        self.site = Site.objects.filter(is_default_site=True).first()
        if not self.site:
            self.site = Site.objects.create(
                hostname="testserver",
                root_page=self.root,
                is_default_site=True
            )

        # Create HomePage cleanly
        self.home = HomePage(title="Home", slug="home")
        self.root.add_child(instance=self.home)

        # Create ArticleIndexPage
        self.article_index = ArticleIndexPage(title="Articles", slug="articles")
        self.home.add_child(instance=self.article_index)

        self.article = Article(
            title="Test Article",
            slug="test-article",
            date=date(2026, 1, 1),
            body=[('paragraph', '<p>English Body Content</p>')]
        )
        self.article_index.add_child(instance=self.article)
        self.article = Article.objects.get(pk=self.article.pk)

    def test_serve_pdf_anonymous_user_redirect(self):
        """Anonymous users are redirected back to the article page."""
        request = self.factory.get(self.article.url + "pdf/")
        request.user = AnonymousUser()
        request.session = MockSession()

        response = self.article.serve_pdf(request)
        # Should redirect back to article page
        self.assertIsInstance(response, HttpResponseRedirect)
        self.assertEqual(response.url, self.article.url)

    def test_serve_pdf_unsubscribed_user_redirect(self):
        """Unsubscribed users are redirected back to the article page."""
        unsubscribed_user = User.objects.create_user(
            phone_number="+915555555555",
            name="Unsubscribed User",
            password="password123"
        )
        request = self.factory.get(self.article.url + "pdf/")
        request.user = unsubscribed_user
        request.session = MockSession()

        response = self.article.serve_pdf(request)
        self.assertIsInstance(response, HttpResponseRedirect)
        self.assertEqual(response.url, self.article.url)

    @patch('articles.models.HTML')
    def test_serve_pdf_subscribed_user_success(self, mock_html):
        """Subscribed users successfully get the PDF download with custom filename headers."""
        subscribed_user = User.objects.create_user(
            phone_number="+914444444444",
            name="Subscribed User",
            password="password123"
        )
        # Activate subscription
        subscribed_user.subscription_plan = "1_month"
        from django.utils import timezone
        subscribed_user.subscription_start = timezone.now()
        subscribed_user.subscription_end = timezone.now() + timezone.timedelta(days=30)
        subscribed_user.save()

        # Mock WeasyPrint PDF writing to prevent dependency failures
        mock_instance = mock_html.return_value
        mock_instance.write_pdf.return_value = b"%PDF-1.4 mock pdf data"

        request = self.factory.get(self.article.url + "pdf/?lang=en")
        request.user = subscribed_user
        request.session = MockSession()

        response = self.article.serve_pdf(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn("attachment; filename*=UTF-8''", response['Content-Disposition'])
        self.assertTrue(response.content.startswith(b"%PDF"))
