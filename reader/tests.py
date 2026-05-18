import csv
from datetime import date
from io import StringIO
from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in
from django.utils import timezone
from django.urls import reverse
from django.contrib.messages.storage.fallback import FallbackStorage
from django.http import Http404, JsonResponse

from reader.models import ReaderUser, PaymentDetails, PLANS
from issue.models import Issue, IssueIndexPage, Volume, Topic
from articles.models import Article, ArticleIndexPage
from home.models import HomePage
from wagtail.models import Page, Site

User = get_user_model()


class ReaderUserManagerTests(TestCase):
    """
    Tests for ReaderUserManager validation and superuser setups.
    """

    def test_create_user_raises_value_error_for_missing_phone_number(self):
        """User creation must raise ValueError if phone number is not supplied."""
        with self.assertRaises(ValueError) as context:
            User.objects.create_user(phone_number="", name="John Doe", password="password")
        self.assertEqual(str(context.exception), "The Phone Number must be set")

    def test_create_user_raises_value_error_for_missing_name(self):
        """User creation must raise ValueError if name is not supplied."""
        with self.assertRaises(ValueError) as context:
            User.objects.create_user(phone_number="+919876543210", name="", password="password")
        self.assertEqual(str(context.exception), "The Name must be set")

    def test_create_superuser_checks_is_staff_and_is_superuser(self):
        """Superuser creation must enforce is_staff=True and is_superuser=True."""
        with self.assertRaises(ValueError) as context:
            User.objects.create_superuser(
                phone_number="+919876543210",
                name="Admin",
                password="password",
                is_staff=False
            )
        self.assertEqual(str(context.exception), "Superuser must have is_staff=True.")

        with self.assertRaises(ValueError) as context:
            User.objects.create_superuser(
                phone_number="+919876543210",
                name="Admin",
                password="password",
                is_superuser=False
            )
        self.assertEqual(str(context.exception), "Superuser must have is_superuser=True.")


class ReaderUserModelAndPropertiesTests(TestCase):
    """
    Tests for ReaderUser properties, deactivation, and subscription states.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            phone_number="+919900990099",
            name="Alice Smith",
            password="alicepassword"
        )

    def test_is_profile_complete(self):
        """Profile completeness requires gender, date_of_birth, address, city, state, and pincode."""
        self.assertFalse(self.user.is_profile_complete)

        self.user.gender = "സ്ത്രീ"
        self.user.date_of_birth = date(1995, 5, 5)
        self.user.address_line_1 = "Baker St"
        self.user.city = "London"
        self.user.state = "Kerala"
        self.user.pincode = "682024"
        self.user.save()

        self.assertTrue(self.user.is_profile_complete)

    def test_subscription_states(self):
        """Test is_subscribed, grace period, and status_display configurations."""
        # No subscription initially
        self.assertFalse(self.user.is_subscribed)
        self.assertFalse(self.user.is_in_grace_period)
        self.assertEqual(self.user.status_display(), "No Subscription")
        self.assertIsNone(self.user.days_until_expiry)

        # Activate subscription
        self.user.activate_subscription("1_month")
        self.assertTrue(self.user.is_subscribed)
        self.assertFalse(self.user.is_in_grace_period)
        self.assertEqual(self.user.status_display(), "Active")
        self.assertGreater(self.user.days_until_expiry, 28)

        # Lapsed into grace period
        now = timezone.now()
        self.user.subscription_end = now - timezone.timedelta(hours=12)
        self.user.save()
        self.assertFalse(self.user.is_subscribed)
        self.assertTrue(self.user.is_in_grace_period)
        self.assertEqual(self.user.status_display(), "Grace Period")

        # Expired completely (past grace period)
        self.user.subscription_end = now - timezone.timedelta(days=4)
        self.user.save()
        self.assertFalse(self.user.is_subscribed)
        self.assertFalse(self.user.is_in_grace_period)
        self.assertEqual(self.user.status_display(), "Expired")

    def test_deactivate_lifecycle(self):
        """Soft deactivation sets is_active=False, deactivation timestamp, and opts out of marketing."""
        self.user.newsletter_opt_in = True
        self.user.save()

        self.user.deactivate()
        self.assertFalse(self.user.is_active)
        self.assertFalse(self.user.newsletter_opt_in)
        self.assertIsNotNone(self.user.deactivated_at)


class ReaderSignalsTests(TestCase):
    """
    Tests auditing signals like recording remote IP on login.
    """

    def test_update_user_login_ip(self):
        user = User.objects.create_user(
            phone_number="+918800880088",
            name="IP Tester",
            password="password"
        )
        factory = RequestFactory()
        request = factory.post("/login/")
        request.META['REMOTE_ADDR'] = "192.168.1.100"

        user_logged_in.send(sender=user.__class__, request=request, user=user)
        user.refresh_from_db()
        self.assertEqual(user.last_login_ip, "192.168.1.100")


class ReaderViewsTests(TestCase):
    """
    Integration tests for payment flows, checkouts, and print circulation admin endpoints.
    """

    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(
            phone_number="+917700770077",
            name="Charlie Brown",
            password="charliepassword"
        )

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

        # Create ArticleIndexPage
        self.article_index = ArticleIndexPage(title="Articles", slug="articles")
        self.home.add_child(instance=self.article_index)

        self.article = Article(title="Gated Art", slug="gated-art", date=date(2026, 1, 1))
        self.article_index.add_child(instance=self.article)
        self.article = Article.objects.get(pk=self.article.pk)

    def test_reader_profile_view(self):
        """Profile view renders successfully for authenticated user."""
        request = self.factory.get("/reader/profile/")
        request.user = self.user

        from reader.views import reader_profile
        response = reader_profile(request)
        self.assertEqual(response.status_code, 200)

    def test_reader_checkout_view_valid_plan(self):
        """Valid subscription plan selection successfully renders checkout page."""
        request = self.factory.get("/reader/checkout/1_month/")
        request.user = self.user

        from reader.views import reader_checkout
        response = reader_checkout(request, plan_type="1_month")
        self.assertEqual(response.status_code, 200)

    def test_reader_checkout_view_invalid_plan(self):
        """Invalid plan redirects user back to profile page."""
        request = self.factory.get("/reader/checkout/invalid/")
        request.user = self.user
        # Setup session support for messaging framework fallback
        request.session = {}
        request._messages = FallbackStorage(request)

        from reader.views import reader_checkout
        response = reader_checkout(request, plan_type="invalid")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/reader/profile/")

    def test_process_payment_idempotency_prevents_duplicate_processing(self):
        """Simulated payment processor uses idempotency key to prevent double transaction executions."""
        from reader.views import process_payment
        idempotency_key = "IK_unique_test_key_123"

        request = self.factory.post("/reader/payment/process/", {
            "plan_type": "1_month",
            "payment_method": "upi",
            "idempotency_key": idempotency_key
        })
        request.user = self.user
        request.session = {}
        request._messages = FallbackStorage(request)

        # First Payment
        response = process_payment(request)
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_subscribed)
        self.assertEqual(PaymentDetails.objects.filter(idempotency_key=idempotency_key).count(), 1)

        # Reset plan for second request
        self.user.subscription_plan = "none"
        self.user.subscription_end = None
        self.user.save()

        # Second request with identical key
        response_dup = process_payment(request)
        self.assertEqual(response_dup.status_code, 302)
        self.user.refresh_from_db()
        # Should NOT be subscribed again since idempotency blocks execution
        self.assertFalse(self.user.is_subscribed)

    def test_toggle_favorite_article_api(self):
        """AJAX Toggle Favorite Article correctly changes relationship status."""
        from reader.views import toggle_favorite_article
        request = self.factory.post(f"/reader/favorite/article/{self.article.id}/")
        request.user = self.user

        # Add Favorite
        response = toggle_favorite_article(request, article_id=self.article.id)
        self.assertEqual(response.status_code, 200)
        data = response.content.decode('utf-8')
        self.assertIn('"favorited": true', data)
        self.assertTrue(self.user.favorite_articles.filter(pk=self.article.id).exists())

        # Remove Favorite
        response_remove = toggle_favorite_article(request, article_id=self.article.id)
        self.assertEqual(response_remove.status_code, 200)
        data_remove = response_remove.content.decode('utf-8')
        self.assertIn('"favorited": false', data_remove)
        self.assertFalse(self.user.favorite_articles.filter(pk=self.article.id).exists())

    def test_export_mailing_list_csv_staff_restricted(self):
        """export_mailing_list creates correct mailing list CSV only for staff/superusers."""
        # Non-staff user
        request = self.factory.get("/reader/export-mailing-list/")
        request.user = self.user

        from reader.views import export_mailing_list
        response = export_mailing_list(request)
        # staff required returns a JSON error from the superuser decorator
        self.assertEqual(response.status_code, 403)

        # Create active print subscriber
        print_subscriber = User.objects.create_user(
            phone_number="+916600660066",
            name="Print Guy",
            password="password"
        )
        print_subscriber.is_print_subscriber = True
        print_subscriber.print_delivery_status = 'active'
        print_subscriber.print_expiry_date = date.today() + timezone.timedelta(days=10)
        print_subscriber.address_line_1 = "Print Street"
        print_subscriber.city = "City"
        print_subscriber.state = "Kerala"
        print_subscriber.pincode = "682025"
        print_subscriber.save()

        # Admin user
        admin_user = User.objects.create_superuser(
            phone_number="+915500550055",
            name="Admin Staff",
            password="password"
        )
        request_admin = self.factory.get("/reader/export-mailing-list/")
        request_admin.user = admin_user

        response_admin = export_mailing_list(request_admin)
        self.assertEqual(response_admin.status_code, 200)
        self.assertEqual(response_admin['Content-Type'], 'text/csv')
        self.assertIn("attachment; filename=", response_admin['Content-Disposition'])

        # Decode CSV to verify contents
        csv_content = response_admin.content.decode('utf-8')
        f = StringIO(csv_content)
        reader = csv.reader(f)
        rows = list(reader)
        self.assertGreater(len(rows), 1)  # header + at least 1 record
        # Verify row fields
        names = [r[0] for r in rows]
        self.assertIn("Print Guy", names)
