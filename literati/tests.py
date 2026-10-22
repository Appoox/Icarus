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
        # Without a browser User-Agent the hit filter treats the request as a bot.
        request = self.factory.get(
            self.literati.url,
            HTTP_USER_AGENT=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
        )
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
from django.contrib.sessions.backends.db import SessionStore
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
        with self.assertRaises(ValidationError) as cm:
            lit.full_clean(exclude=['path', 'depth'])
        self.assertIn('phone_number', cm.exception.message_dict)

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
        # Should pass cleanly — save(), not validation, handles the merge.
        # path/depth are excluded: an unsaved page has no tree position yet.
        try:
            lit.full_clean(exclude=['path', 'depth'])
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
        with self.assertRaises(ValidationError) as cm:
            lit2.full_clean(exclude=['path', 'depth'])
        self.assertIn('phone_number', cm.exception.message_dict)

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
        with self.assertRaises(ValidationError) as cm:
            lit.full_clean(exclude=['path', 'depth'])
        self.assertIn('email', cm.exception.message_dict)

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
        with self.assertRaises(ValidationError) as cm:
            lit2.full_clean(exclude=['path', 'depth'])
        self.assertIn('email', cm.exception.message_dict)

    def test_save_creates_linked_reader_user(self):
        """
        Literati.save() creates a ReaderUser carrying the page's contact
        details, and the after_create_page hook does not create a second one.
        """
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

        self.assertIsNotNone(lit.reader_user)
        user_pk = lit.reader_user.pk

        request = self.factory.get("/")
        request.session = SessionStore()
        setattr(request, '_messages', FallbackStorage(request))
        after_create_literati(request, lit)

        lit.refresh_from_db()
        user = lit.reader_user
        self.assertEqual(user.pk, user_pk)
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
        # No guessable password: the author sets one through password reset.
        self.assertFalse(user.has_usable_password())

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
        request.session = SessionStore()
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

        # save() created and linked the reader account.
        user = lit.reader_user
        self.assertIsNotNone(user)
        self.assertEqual(user.email, "orig@example.com")

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


class AuthorAccountPasswordTests(TestCase):
    """
    Author pages used to give their reader account the password
    "<phone>123" and broadcast it (email, log, admin message). Now no
    password is ever generated: the author gets a one-time set-password
    link, and reset_author_passwords disables the old guessable ones.
    """

    def setUp(self):
        from django.core import mail  # noqa: F401  (outbox is reset per test)

        Site.objects.all().delete()
        for child in Page.get_first_root_node().get_children():
            child.delete()
        root = Page.get_first_root_node()
        self.home = HomePage(title="Home", slug="home")
        root.add_child(instance=self.home)
        Site.objects.create(hostname="testserver", root_page=self.home, is_default_site=True)
        self.author_index = AuthorIndexPage(title="Authors", slug="authors")
        self.home.add_child(instance=self.author_index)
        self.factory = RequestFactory()

    def _request(self):
        request = self.factory.get("/")
        request.session = SessionStore()
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def _author(self, phone, email="", slug=None):
        lit = Literati(title="Asha Author", slug=slug or f"a{phone[-4:]}", phone_number=phone, email=email)
        self.author_index.add_child(instance=lit)
        return lit

    def test_new_author_is_emailed_a_set_password_link(self):
        from django.core import mail

        lit = self._author("+919811100001", email="asha@example.com")
        request = self._request()
        after_create_literati(request, lit)

        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertEqual(mail.outbox[0].to, ["asha@example.com"])
        self.assertIn("/accounts/password/reset/key/", body)
        self.assertNotIn("+919811100001", body)
        self.assertNotIn("123", body.split("/accounts/")[0])  # no password in the text

        # Admin message carries no credentials either.
        shown = [str(m) for m in request._messages]
        self.assertTrue(shown)
        self.assertFalse(any("+919811100001" in m or "Password" in m for m in shown))

        lit.reader_user.refresh_from_db()
        self.assertFalse(lit.reader_user.has_usable_password())

    def test_set_password_link_opens_the_reset_form(self):
        from django.core import mail

        lit = self._author("+919811100002", email="b@example.com")
        after_create_literati(self._request(), lit)
        link = next(line for line in mail.outbox[0].body.splitlines() if "/password/reset/key/" in line)
        path = link.split("testserver", 1)[1]

        response = self.client.get(path)
        # allauth moves the token into the session and redirects to the form.
        self.assertEqual(response.status_code, 302)
        self.assertIn("set-password", response.url)
        self.assertEqual(self.client.get(response.url).status_code, 200)

    def test_author_without_email_gets_no_mail(self):
        from django.core import mail

        lit = self._author("+919811100003")
        after_create_literati(self._request(), lit)
        self.assertEqual(len(mail.outbox), 0)

    def test_existing_reader_with_password_is_left_alone(self):
        from django.core import mail

        User.objects.create_user(phone_number="+919811100004", name="Reader", password="own-choice", email="r@example.com")
        lit = self._author("+919811100004", email="r@example.com")
        after_create_literati(self._request(), lit)
        self.assertEqual(len(mail.outbox), 0)
        lit.reader_user.refresh_from_db()
        self.assertTrue(lit.reader_user.check_password("own-choice"))

    def test_reset_author_passwords_disables_only_guessable_ones(self):
        from io import StringIO

        from django.core import mail
        from django.core.management import call_command

        weak = self._author("+919811100005", email="weak@example.com")
        weak.reader_user.set_password("+919811100005123")
        weak.reader_user.save()
        strong = self._author("+919811100006", email="strong@example.com")
        strong.reader_user.set_password("a-real-passphrase")
        strong.reader_user.save()

        out = StringIO()
        call_command("reset_author_passwords", "--dry-run", stdout=out)
        self.assertIn("1 author account(s)", out.getvalue())
        weak.reader_user.refresh_from_db()
        self.assertTrue(weak.reader_user.has_usable_password())

        out = StringIO()
        call_command("reset_author_passwords", "--email", stdout=out)
        weak.reader_user.refresh_from_db()
        strong.reader_user.refresh_from_db()
        self.assertFalse(weak.reader_user.has_usable_password())
        self.assertTrue(strong.reader_user.check_password("a-real-passphrase"))
        self.assertEqual([m.to for m in mail.outbox], [["weak@example.com"]])
        self.assertNotIn("+91", out.getvalue())


class LiteratiAuditPrivacyTests(TestCase):
    def test_author_contact_details_stay_out_of_the_audit_log(self):
        import json

        from auditlog import get_logentry_model
        from django.contrib.contenttypes.models import ContentType

        from literati.apps import LITERATI_AUDIT_EXCLUDED_FIELDS

        root = Page.get_first_root_node()
        index = AuthorIndexPage(title="Authors", slug="authors-audit")
        root.add_child(instance=index)
        lit = Literati(title="Audited", slug="audited", phone_number="+919811200001", email="a@example.com")
        index.add_child(instance=lit)
        lit.city = "Hidden City"
        lit.role = "Columnist"
        lit.save()

        LogEntry = get_logentry_model()
        rows = LogEntry.objects.filter(content_type=ContentType.objects.get_for_model(Literati), object_id=lit.pk)
        changes = rows.order_by("-pk").first().changes
        changes = changes if isinstance(changes, dict) else json.loads(changes)
        self.assertIn("role", changes)
        for field in LITERATI_AUDIT_EXCLUDED_FIELDS:
            self.assertNotIn(field, changes)
        dumped = json.dumps(list(rows.values("changes")), default=str)
        self.assertNotIn("+919811200001", dumped)
        self.assertNotIn("Hidden City", dumped)
