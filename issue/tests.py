from datetime import date
from unittest.mock import MagicMock

from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.http import HttpResponseRedirect, Http404
from wagtail.models import Page, Site
from wagtail.test.utils import WagtailPageTestCase
from wagtail.documents.models import Document
from django.contrib.auth.models import AnonymousUser

from issue.models import Issue, IssueIndexPage, Volume, Topic, IssueArticleReprint, VolumeForm, IssueForm
from articles.models import Article, ArticleIndexPage
from literati.models import Literati, EditorialBoard, EditorialBoardMember, AuthorIndexPage
from home.models import HomePage

User = get_user_model()


class VolumeTests(TestCase):
    """
    Tests for the Volume model, custom string representation, and VolumeForm pre-fill logic.
    """

    def test_volume_str_single_year(self):
        """Test volume string formatting for a single year volume."""
        vol = Volume(number=1, year_start=2026)
        self.assertEqual(str(vol), "Volume 1 (2026)")

    def test_volume_str_spanned_years(self):
        """Test volume string formatting for a volume spanning multiple years."""
        vol = Volume(number=2, year_start=2025, year_end=2026)
        self.assertEqual(str(vol), "Volume 2 (2025–2026)")

    def test_volume_form_prefills_number(self):
        """Test that VolumeForm pre-fills the next volume number correctly (last + 1)."""
        # Create an existing volume
        Volume.objects.create(number=5, year_start=2020)

        # Instantiate a new VolumeForm Class properly
        from django.forms.models import modelform_factory
        VolumeFormClass = modelform_factory(Volume, form=VolumeForm, fields="__all__")
        form = VolumeFormClass()
        self.assertEqual(form.fields['number'].initial, 6)


class IssueModelAndFormTests(WagtailPageTestCase):
    """
    Tests for Issue model properties, index page context, and pre-filling form logic.
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

        # Ensure default site
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

        self.author_index = AuthorIndexPage(title="Authors", slug="authors")
        self.home.add_child(instance=self.author_index)

        self.literati = Literati(title="John Doe", slug="john-doe", role="Editor", phone_number="+919876543213")
        self.author_index.add_child(instance=self.literati)

        # Editorial Board Setup
        self.board = EditorialBoard.objects.create(name="Editorial Board 2026")
        self.board_member = EditorialBoardMember.objects.create(
            board=self.board,
            editor=self.literati,
            role="editor"
        )

        self.volume = Volume.objects.create(number=1, year_start=2026)

        self.issue_index = IssueIndexPage(title="Issues", slug="issues")
        self.home.add_child(instance=self.issue_index)

        self.issue = Issue(
            title="January 2026",
            slug="jan-2026",
            volume=self.volume,
            issue_number=1,
            date_of_publishing=date(2026, 1, 1),
            editorial_board=self.board
        )
        self.issue_index.add_child(instance=self.issue)
        self.issue = Issue.objects.get(pk=self.issue.pk)

        self.article_index = ArticleIndexPage(title="Articles", slug="articles")
        self.home.add_child(instance=self.article_index)

    def test_issue_board_members(self):
        """Test board_members helper property returns the correct list of editors."""
        members = self.issue.board_members
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].editor, self.literati)

    def test_get_all_articles(self):
        """Test compiling primary articles and reprinted articles for an issue."""
        # Primary article
        primary_art = Article(
            title="Primary",
            slug="primary",
            date=date(2026, 1, 1),
            main_issue=self.issue
        )
        self.article_index.add_child(instance=primary_art)

        # Reprint article (different main issue, but reprinted in this one)
        other_issue = Issue(
            title="Feb 2026",
            slug="feb-2026",
            volume=self.volume,
            issue_number=2,
            date_of_publishing=date(2026, 2, 1)
        )
        self.issue_index.add_child(instance=other_issue)

        reprint_art = Article(
            title="Reprint",
            slug="reprint",
            date=date(2026, 2, 1),
            main_issue=other_issue
        )
        self.article_index.add_child(instance=reprint_art)

        IssueArticleReprint.objects.create(
            issue=self.issue,
            article=reprint_art
        )

        all_articles = self.issue.get_all_articles()
        self.assertEqual(len(all_articles), 2)
        titles = [a.title for a in all_articles]
        self.assertIn("Primary", titles)
        self.assertIn("Reprint", titles)

    def test_issue_index_page_context(self):
        """Test that IssueIndexPage.get_context compiles the list of child issues."""
        request = self.factory.get("/issues/")
        context = self.issue_index.get_context(request)
        self.assertIn("issues", context)
        self.assertEqual(len(context["issues"]), 1)
        self.assertEqual(context["issues"][0].id, self.issue.id)

    def test_issue_form_prefills_title_and_volume(self):
        """Test IssueForm pre-fills with default Volume and next Malayalam calendar month."""
        from django.forms.models import modelform_factory
        IssueFormClass = modelform_factory(Issue, form=IssueForm, fields="__all__")
        form = IssueFormClass()
        # Checks if next calendar month title is generated correctly (Malayalam string format)
        self.assertTrue(len(form.initial.get('title', '')) > 0)
        self.assertEqual(form.fields['volume'].initial, self.volume.pk)


class IssueRoutablePdfTests(TestCase):
    """
    Tests for the Issue serve_pdf routable view, permission gates, 404 behavior, and success file serving.
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

        # Ensure default site is set up
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

        self.issue_index = IssueIndexPage(title="Issues", slug="issues")
        self.home.add_child(instance=self.issue_index)

        self.volume = Volume.objects.create(number=1, year_start=2026)
        self.issue = Issue(
            title="January 2026",
            slug="jan-2026",
            volume=self.volume,
            issue_number=1,
            date_of_publishing=date(2026, 1, 1)
        )
        self.issue_index.add_child(instance=self.issue)
        self.issue = Issue.objects.get(pk=self.issue.pk)

    def test_serve_pdf_anonymous_user_redirect(self):
        """Anonymous users serving issue PDF redirected to issue URL."""
        request = self.factory.get(self.issue.url + "pdf/")
        request.user = AnonymousUser()

        response = self.issue.serve_pdf(request)
        self.assertIsInstance(response, HttpResponseRedirect)
        self.assertEqual(response.url, self.issue.url)

    def test_serve_pdf_no_file_uploaded_404(self):
        """Subscribed users trying to download a non-existent PDF receive a 404 error."""
        subscribed_user = User.objects.create_user(
            phone_number="+913333333333",
            name="Subscribed User",
            password="password123"
        )
        subscribed_user.subscription_plan = "1_month"
        from django.utils import timezone
        subscribed_user.subscription_start = timezone.now()
        subscribed_user.subscription_end = timezone.now() + timezone.timedelta(days=30)
        subscribed_user.save()

        request = self.factory.get(self.issue.url + "pdf/")
        request.user = subscribed_user

        with self.assertRaises(Http404):
            self.issue.serve_pdf(request)

    def test_serve_pdf_success(self):
        """Subscribed users successfully download the attached issue PDF document."""
        subscribed_user = User.objects.create_user(
            phone_number="+912222222222",
            name="Subscribed User",
            password="password123"
        )
        subscribed_user.subscription_plan = "1_month"
        from django.utils import timezone
        subscribed_user.subscription_start = timezone.now()
        subscribed_user.subscription_end = timezone.now() + timezone.timedelta(days=30)
        subscribed_user.save()

        # Create a Wagtail Document object with actual mock file data
        from django.core.files.base import ContentFile
        doc_file = ContentFile(b"%PDF-1.4 issue mock pdf bytes", name="issue_test.pdf")
        real_doc = Document.objects.create(title="January 2026 PDF", file=doc_file)
        self.issue.PDF_file = real_doc

        request = self.factory.get(self.issue.url + "pdf/")
        request.user = subscribed_user

        response = self.issue.serve_pdf(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn("attachment; filename*=UTF-8''", response['Content-Disposition'])
        self.assertEqual(response.content, b"%PDF-1.4 issue mock pdf bytes")


class TopicTests(TestCase):
    """Simple tests for the Topic snippet."""

    def test_topic_str(self):
        topic = Topic(name="Science", slug="science")
        self.assertEqual(str(topic), "Science")
