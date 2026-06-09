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
            email="watson@bakerstreet.com",
            phone_number="+919876543210"
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
        literati_a = Literati(title="Arthur Dent", slug="arthur-dent", phone_number="+919876543211")
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

        self.editor = Literati(title="Chief Editor", slug="chief-editor", phone_number="+919876543212")
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


from django.core.exceptions import ValidationError
from django.contrib.messages.storage.fallback import FallbackStorage
from literati.wagtail_hooks import after_create_literati

class LiteratiAutoCreationAndValidationTests(TestCase):
    def setUp(self):
        # Setup page tree cleanly via instance-level deletes to prevent treebeard corruption
        Site.objects.all().delete()
        for p in Page.objects.filter(slug__in=['home', 'authors']):
            try:
                p.delete()
            except Exception:
                pass
        self.root = Page.get_first_root_node()
        self.home = HomePage(title="Home", slug="home")
        self.root.add_child(instance=self.home)
        self.author_index = AuthorIndexPage(title="Authors", slug="authors")
        self.home.add_child(instance=self.author_index)
        self.factory = RequestFactory()

    def test_phone_number_required(self):
        """Verify that validation fails if phone number is empty."""
        lit = Literati(
            title="No Phone",
            slug="no-phone",
            email="nophone@example.com"
        )
        # Note: blank=False is validated by Django's full_clean()
        with self.assertRaises(ValidationError):
            lit.full_clean()

    def test_phone_shared_with_reader_does_not_block_create(self):
        """
        Creating a Literati whose phone matches an existing ReaderUser must NOT
        raise ValidationError. The after_create_page hook auto-links them instead.
        This is the normal real-world case: a reader who later becomes an author.
        """
        phone = "+919999999999"
        User.objects.create_user(phone_number=phone, name="Existing Reader")

        lit = Literati(
            title="New Literati",
            slug="new-literati",
            phone_number=phone,
        )
        # Should pass cleanly — the hook, not validation, handles the merge
        try:
            lit.full_clean()
        except ValidationError as e:
            self.fail(f"full_clean() raised ValidationError unexpectedly: {e}")

    def test_validation_phone_taken_by_another_literati(self):
        """Validation fails if phone number is already registered on another Literati page."""
        phone = "+919999999999"
        lit1 = Literati(
            title="First Literati",
            slug="first-lit",
            phone_number=phone
        )
        self.author_index.add_child(instance=lit1)
        
        lit2 = Literati(
            title="Second Literati",
            slug="second-lit",
            phone_number=phone
        )
        # We don't need to add_child, just clean to verify
        lit2.parent = self.author_index
        with self.assertRaises(ValidationError):
            lit2.full_clean()

    def test_validation_email_taken_by_reader_user(self):
        """Validation fails if email is already registered in ReaderUser."""
        email = "reader@example.com"
        User.objects.create_user(phone_number="+919999999998", name="Existing Reader", email=email)
        
        lit = Literati(
            title="New Literati",
            slug="new-literati",
            phone_number="+919999999999",
            email=email
        )
        with self.assertRaises(ValidationError):
            lit.full_clean()

    def test_validation_email_taken_by_another_literati(self):
        """Validation fails if email is already registered on another Literati page."""
        email = "lit@example.com"
        lit1 = Literati(
            title="First Literati",
            slug="first-lit",
            phone_number="+919999999998",
            email=email
        )
        self.author_index.add_child(instance=lit1)
        
        lit2 = Literati(
            title="Second Literati",
            slug="second-lit",
            phone_number="+919999999999",
            email=email
        )
        lit2.parent = self.author_index
        with self.assertRaises(ValidationError):
            lit2.full_clean()

    def test_after_create_hook_creates_reader_user(self):
        """Wagtail hook automatically creates a ReaderUser and links it to the Literati."""
        phone = "+919999999999"
        lit = Literati(
            title="Jane Doe",
            slug="jane-doe",
            phone_number=phone,
            email="jane@example.com",
            address_line_1="123 Baker Street",
            address_line_2="Apt 4B",
            city="London",
            post_office="Baker Street PO",
            pincode="682025",
            district="Ernakulam",
            state="Kerala"
        )
        self.author_index.add_child(instance=lit)

        self.assertIsNone(lit.reader_user)

        request = self.factory.get("/")
        setattr(request, '_messages', FallbackStorage(request))
        after_create_literati(request, lit)

        lit.refresh_from_db()
        self.assertIsNotNone(lit.reader_user)

        user = lit.reader_user
        # phone_number_encrypted is the decrypted display value (Fernet decrypts on access)
        self.assertEqual(user.phone_number_encrypted, phone)
        self.assertEqual(user.name, "Jane Doe")
        self.assertEqual(user.email, "jane@example.com")
        self.assertEqual(user.address_line_1, "123 Baker Street")
        self.assertEqual(user.address_line_2, "Apt 4B")
        self.assertEqual(user.city, "London")
        self.assertEqual(user.post_office, "Baker Street PO")
        self.assertEqual(user.pincode, "682025")
        self.assertEqual(user.district, "Ernakulam")
        self.assertEqual(user.state, "Kerala")

        # Password follows the pattern: stripped E.164 number + "123"
        clean_phone = phone.replace(' ', '').replace('-', '')
        self.assertTrue(user.check_password(f"{clean_phone}123"))

    def test_auto_link_existing_reader_on_literati_create(self):
        """
        When creating a Literati whose phone already belongs to a ReaderUser,
        the hook links the existing reader instead of creating a duplicate account.
        The existing reader's name is updated to match the Literati title.
        """
        phone = "+919999999988"
        existing = User.objects.create_user(
            phone_number=phone, name="Original Reader Name"
        )
        original_pk = existing.pk

        lit = Literati(
            title="Author Name",
            slug="author-name",
            phone_number=phone,
        )
        self.author_index.add_child(instance=lit)

        request = self.factory.get("/")
        setattr(request, '_messages', FallbackStorage(request))
        after_create_literati(request, lit)

        lit.refresh_from_db()
        # Must link to the EXISTING reader, not create a new one
        self.assertIsNotNone(lit.reader_user)
        self.assertEqual(lit.reader_user.pk, original_pk)
        # No extra reader accounts should have been created
        from reader.models import ReaderUser
        hashed = ReaderUser.hash_phone(phone)
        self.assertEqual(
            ReaderUser.objects.filter(phone_number_hash=hashed).count(), 1
        )

    def test_field_syncs_on_save(self):
        """Saving Literati page syncs title, phone, email, and address fields to the linked ReaderUser."""
        phone = "+919999999999"
        lit = Literati(
            title="Original Name",
            slug="orig-name",
            phone_number=phone,
            email="orig@example.com",
            address_line_1="Original Address 1",
            address_line_2="Original Address 2",
            city="Original City",
            post_office="Original PO",
            pincode="682025",
            district="Original District",
            state="Kerala"
        )
        self.author_index.add_child(instance=lit)

        user = User.objects.create_user(
            phone_number=phone,
            name="Original Name",
            email="orig@example.com",
            address_line_1="Original Address 1",
            address_line_2="Original Address 2",
            city="Original City",
            post_office="Original PO",
            pincode="682025",
            district="Original District",
            state="Kerala"
        )
        lit.reader_user = user
        lit.save()

        # Update all Literati fields
        lit.title          = "Updated Name"
        lit.phone_number   = "+919999999990"
        lit.email          = "updated@example.com"
        lit.address_line_1 = "Updated Address 1"
        lit.address_line_2 = "Updated Address 2"
        lit.city           = "Updated City"
        lit.post_office    = "Updated PO"
        lit.pincode        = "682001"
        lit.district       = "Updated District"
        lit.state          = "Tamil Nadu"
        lit.save()

        user.refresh_from_db()
        self.assertEqual(user.name,              "Updated Name")
        # phone_number_encrypted is the decrypted display value (Fernet decrypts on access)
        self.assertEqual(user.phone_number_encrypted, "+919999999990")
        self.assertEqual(user.email,             "updated@example.com")
        self.assertEqual(user.address_line_1,    "Updated Address 1")
        self.assertEqual(user.address_line_2,    "Updated Address 2")
        self.assertEqual(user.city,              "Updated City")
        self.assertEqual(user.post_office,       "Updated PO")
        self.assertEqual(user.pincode,           "682001")
        self.assertEqual(user.district,          "Updated District")
        self.assertEqual(user.state,             "Tamil Nadu")
