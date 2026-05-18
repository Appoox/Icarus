from datetime import date

from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from wagtail.models import Page, Site
from wagtail.test.utils import WagtailPageTestCase
from django.contrib.auth.models import AnonymousUser

class MockSession(dict):
    session_key = "test_session_key"


from literati.models import Literati, AuthorIndexPage, ArticleAuthorRelationship, EditorialBoard, EditorialBoardMember
from articles.models import Article, ArticleIndexPage
from home.models import HomePage

User = get_user_model()


class LiteratiModelAndContextTests(WagtailPageTestCase):
    """
    Tests for Literati model helper properties, relationships, and hit-counter context logic.
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

        # Ensure default site is configured
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

        # Create AuthorIndexPage
        self.author_index = AuthorIndexPage(title="Authors", slug="authors")
        self.home.add_child(instance=self.author_index)

        self.literati = Literati(
            title="Dr. John Watson",
            slug="john-watson",
            role="Author & Contributor",
            bio="<p>Biographer of Sherlock Holmes</p>",
            email="watson@bakerstreet.com"
        )
        self.author_index.add_child(instance=self.literati)
        self.literati = Literati.objects.get(pk=self.literati.pk)

        self.article_index = ArticleIndexPage(title="Articles", slug="articles")
        self.home.add_child(instance=self.article_index)

    def test_literati_parent_page_types(self):
        """Verify that Literati pages can only be child nodes of AuthorIndexPage."""
        self.assertAllowedParentPageTypes(Literati, {AuthorIndexPage})

    def test_author_index_page_subpage_types(self):
        """Verify that AuthorIndexPage only allows Literati as child pages."""
        self.assertAllowedSubpageTypes(AuthorIndexPage, {Literati})

    def test_literati_get_articles(self):
        """Test get_articles compiles articles that have the corresponding author relationship."""
        # Create an article
        art1 = Article(
            title="A Study in Scarlet",
            slug="study-in-scarlet",
            date=date(2026, 1, 1)
        )
        self.article_index.add_child(instance=art1)

        # Create relationship
        ArticleAuthorRelationship.objects.create(
            page=art1,
            author=self.literati,
            role="Author"
        )

        articles = self.literati.get_articles()
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].id, art1.id)

    def test_author_index_page_context(self):
        """Test AuthorIndexPage.get_context fetches and alphabetically sorts live children authors."""
        # Create another author to test alphabetical sorting
        literati_a = Literati(title="Arthur Dent", slug="arthur-dent")
        self.author_index.add_child(instance=literati_a)

        request = self.factory.get("/authors/")
        context = self.author_index.get_context(request)
        
        self.assertIn("authors", context)
        authors = context["authors"]
        self.assertEqual(len(authors), 2)
        # Alphabetical sort check: "Arthur Dent" should be first, "Dr. John Watson" second
        self.assertEqual(authors[0].title, "Arthur Dent")
        self.assertEqual(authors[1].title, "Dr. John Watson")

    def test_literati_get_context_anonymous_user_increments_hitcount(self):
        """Anonymous user visits trigger hit count increments."""
        request = self.factory.get(self.literati.url)
        request.user = AnonymousUser()  # Anonymous representation
        request.session = MockSession()

        # Running get_context should increment hitcount
        from hitcount.models import HitCount
        hit_count = HitCount.objects.get_for_object(self.literati)
        initial_hits = hit_count.hits

        # Trigger get_context
        self.literati.get_context(request)
        
        # Reload hit count
        hit_count.refresh_from_db()
        self.assertEqual(hit_count.hits, initial_hits + 1)


class EditorialBoardTests(TestCase):
    """
    Tests for EditorialBoard snippet model and relations.
    """

    def setUp(self):
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

        # Create AuthorIndexPage
        self.author_index = AuthorIndexPage(title="Authors", slug="authors")
        self.home.add_child(instance=self.author_index)

        self.editor = Literati(title="Chief Editor", slug="chief-editor")
        self.author_index.add_child(instance=self.editor)

    def test_editorial_board_str(self):
        """Test string representation of EditorialBoard snippet."""
        board = EditorialBoard(name="Advisory Committee 2026")
        self.assertEqual(str(board), "Advisory Committee 2026")

    def test_editorial_board_members_relationships(self):
        """Test board membership models and role attributes."""
        board = EditorialBoard.objects.create(name="Editorial Board")
        member = EditorialBoardMember.objects.create(
            board=board,
            editor=self.editor,
            role="associate"
        )
        self.assertEqual(board.members.count(), 1)
        self.assertEqual(board.members.first().role, "associate")
        self.assertEqual(board.members.first().editor.id, self.editor.id)
        self.assertEqual(member.get_role_display(), "Associate Editor")
