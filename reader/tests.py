import json
import csv
import unittest
from datetime import date
from io import StringIO
from django.core.management import call_command
from django.test import TestCase, RequestFactory, override_settings
from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in
from django.utils import timezone
from django.urls import reverse
from django.contrib.messages.storage.fallback import FallbackStorage
from django.http import Http404, JsonResponse

from reader.models import ReaderUser, PaymentDetails, PLANS, SubscriptionHistory
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
        """Profile completeness requires gender, birth_year, address, city, state, and pincode."""
        self.assertFalse(self.user.is_profile_complete)

        self.user.gender = "സ്ത്രീ"
        self.user.birth_year = 1995      # was date_of_birth = date(1995, 5, 5)
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
        self.assertEqual(self.user.status_display(), "വരിസംഖ്യ ഇല്ല")
        self.assertIsNone(self.user.days_until_expiry)

        # Activate subscription
        self.user.activate_subscription("1_month")
        self.assertTrue(self.user.is_subscribed)
        self.assertFalse(self.user.is_in_grace_period)
        self.assertEqual(self.user.status_display(), "സജീവം")
        self.assertGreater(self.user.days_until_expiry, 28)

        # Lapsed into grace period
        now = timezone.now()
        self.user.subscription_end = now - timezone.timedelta(hours=12)
        self.user.save()
        self.assertFalse(self.user.is_subscribed)
        self.assertTrue(self.user.is_in_grace_period)
        self.assertEqual(self.user.status_display(), "ഗ്രേസ് കാലയളവ്")

        # Expired completely (past grace period)
        self.user.subscription_end = now - timezone.timedelta(days=4)
        self.user.save()
        self.assertFalse(self.user.is_subscribed)
        self.assertFalse(self.user.is_in_grace_period)
        self.assertEqual(self.user.status_display(), "കാലഹരണപ്പെട്ടു")

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


class SubscriptionHistoryAndGDPRTests(TestCase):
    """
    Tests for SubscriptionHistory, cancellation grace period,
    anonymization/soft delete, and the 8-year purge command.
    """
    def setUp(self):
        self.user = User.objects.create_user(
            phone_number="+919999988888",
            name="John Smith",
            password="testpassword"
        )
        self.payment = PaymentDetails.objects.create(
            gateway_name="Razorpay",
            amount=299.00,
            status="success"
        )

    def test_activate_subscription_creates_history(self):
        """Activating a subscription should create a SubscriptionHistory entry."""
        self.user.payment_details = self.payment
        self.user.activate_subscription("1_month")
        
        histories = SubscriptionHistory.objects.filter(reader=self.user)
        self.assertEqual(histories.count(), 1)
        history = histories.first()
        self.assertEqual(history.subscription_plan, "1_month")
        self.assertTrue(history.is_active)
        self.assertFalse(history.is_cancelled)
        self.assertEqual(history.payment_details, self.payment)

    def test_cancellation_grace_period(self):
        """Cancelling a subscription should not revoke access immediately, but update flags."""
        self.user.payment_details = self.payment
        self.user.activate_subscription("1_month")
        
        # Now cancel subscription via the views or directly
        self.user.is_cancelled = True
        self.user.save()
        
        # User should still be considered subscribed during grace period
        self.assertTrue(self.user.is_subscribed)
        
        # SubscriptionHistory should reflect cancellation
        history = SubscriptionHistory.objects.get(reader=self.user, is_active=True)
        self.assertTrue(history.is_cancelled)
        self.assertTrue(history.is_active)

    def test_anonymize_account(self):
        """Anonymizing a user clears PI but preserves subscription history and is_anonymized flag."""
        self.user.payment_details = self.payment
        self.user.activate_subscription("1_month")
        
        # Perform deactivation then anonymization
        self.user.deactivate()
        self.user.anonymize_account()
        
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_anonymized)
        self.assertEqual(self.user.name, "Archived User")
        self.assertIn("archived_user_", self.user.email)
        self.assertFalse(self.user.is_active)
        
        # Verify subscription history was preserved
        histories = SubscriptionHistory.objects.filter(reader=self.user)
        self.assertEqual(histories.count(), 1)
        self.assertEqual(histories.first().subscription_plan, "1_month")

    def test_purge_expired_data_command(self):
        """Test the purge_expired_data management command deletes records older than 8 years."""
        self.user.payment_details = self.payment
        self.user.activate_subscription("1_month")
        
        self.user.deactivate()
        self.user.anonymize_account()
        
        # Backdate the user's subscription_end to 9 years ago
        nine_years_ago = timezone.now() - timezone.timedelta(days=9 * 365)
        self.user.subscription_end = nine_years_ago
        self.user.save()
        
        history = SubscriptionHistory.objects.get(reader=self.user)
        history.subscription_end = nine_years_ago
        history.save()
        
        # Call command
        call_command('purge_expired_data')
        
        # The user and history should now be permanently deleted
        self.assertFalse(User.objects.filter(id=self.user.id).exists())
        self.assertFalse(SubscriptionHistory.objects.filter(id=history.id).exists())


class SignupAgeGateTests(TestCase):
    """
    Age assurance on the signup form: a neutral birth-year dropdown replaces
    the old "I am 18+" checkbox.  CustomSignupForm.clean() computes the age
    and refuses under-18 signups with a non-field error carrying
    code='underage' — the code ReaderSignupView keys on to redirect to the
    full-screen guardian explainer page.  TypedChoiceField rejects any
    tampered out-of-range year as a plain field error.

    Other required fields (phone, email, password) are intentionally omitted:
    those raise their own field errors, but the age gate must behave the same
    regardless — so these tests assert only on the age-gate outcome.
    """

    def _form_with_birth_year(self, birth_year):
        from reader.auth_forms import CustomSignupForm
        return CustomSignupForm(data={
            'name': 'Test Reader',
            'birth_year': str(birth_year),   # dropdowns POST strings
            'accept_terms': 'on',
        })

    def _form_for_age(self, age):
        return self._form_with_birth_year(timezone.now().year - age)

    def _error_codes(self, form):
        form.is_valid()
        return [e.code for e in form.non_field_errors().as_data()]

    def test_under_18_birth_year_is_refused_with_underage_code(self):
        """A 10-year-old's birth year yields the 'underage' non-field error."""
        form = self._form_for_age(10)
        self.assertFalse(form.is_valid())
        self.assertIn('underage', self._error_codes(form))
        # The refusal is routing, not a complaint about the field itself.
        self.assertNotIn('birth_year', form.errors)

    def test_exactly_18_passes_age_gate(self):
        """Someone turning 18 this year clears the gate (>= 18)."""
        form = self._form_for_age(18)
        form.is_valid()  # invalid overall (phone/password missing)
        self.assertNotIn('birth_year', form.errors)
        self.assertFalse(form.non_field_errors())

    def test_17_is_refused_but_18_is_not(self):
        """The threshold sits exactly between 17 (refused) and 18 (allowed)."""
        self.assertIn('underage', self._error_codes(self._form_for_age(17)))
        self.assertNotIn('underage', self._error_codes(self._form_for_age(18)))

    def test_adult_birth_year_passes_age_gate(self):
        """A comfortably-adult birth year never produces an age error."""
        form = self._form_for_age(30)
        form.is_valid()
        self.assertNotIn('birth_year', form.errors)
        self.assertFalse(form.non_field_errors())

    def test_out_of_range_year_is_a_field_error_not_a_pass(self):
        """Years outside the dropdown (tampered POST) fail as field errors,
        never as an age-gate pass or an underage redirect."""
        for bad_year in (1500, timezone.now().year + 1):
            form = self._form_with_birth_year(bad_year)
            self.assertFalse(form.is_valid())
            self.assertIn('birth_year', form.errors)
            self.assertFalse(form.non_field_errors())

    def test_signup_form_has_no_field_that_writes_birth_year(self):
        """
        The birth year is transient: it is computed in clean() and never saved.
        Guard that invariant structurally — the form must expose birth_year for
        the age question but must NOT declare it as a model-writing field, and
        signup() must not assign user.birth_year.
        """
        import inspect
        from reader.auth_forms import CustomSignupForm

        form = CustomSignupForm()
        self.assertIn('birth_year', form.fields)  # the question is asked
        # signup() records the 18+ declaration but must not persist the raw year.
        signup_src = inspect.getsource(CustomSignupForm.signup)
        self.assertNotIn('user.birth_year', signup_src)
        self.assertIn('user.is_above_18', signup_src)

    def test_dropdown_offers_current_year_down_to_1900(self):
        """The dropdown's selectable range is [1900, current year] plus the
        empty prompt — nothing older, nothing in the future."""
        from reader.auth_forms import CustomSignupForm
        years = [c[0] for c in CustomSignupForm().fields['birth_year'].choices
                 if c[0] != '']
        self.assertEqual(years[0], timezone.now().year)
        self.assertEqual(years[-1], 1900)


class GuardianRedirectViewTests(TestCase):
    """
    View-level behaviour of the age gate: an under-18 signup POST redirects to
    the full-screen guardian explainer page and creates no user, while the
    page itself is real, linkable, 200 content.
    """

    def setUp(self):
        # base.html needs a wagtail Site + home page to render.
        Site.objects.all().delete()
        for p in Page.objects.filter(slug='home'):
            try:
                p.delete()
            except Exception:
                pass
        root = Page.get_first_root_node()
        self.site = Site.objects.create(
            hostname="testserver", root_page=root, is_default_site=True
        )
        self.home = HomePage(title="Home", slug="home")
        root.add_child(instance=self.home)

    def test_under_18_signup_post_redirects_to_guardian_page(self):
        """An under-18 POST never re-renders the form: it redirects to the
        guardian page, and the refused signup persists nothing."""
        response = self.client.post(reverse('account_signup'), {
            'name': 'Kid Reader',
            'birth_year': str(timezone.now().year - 12),
            'accept_terms': 'on',
        })
        self.assertRedirects(
            response, reverse('guardian_account_info'),
            fetch_redirect_response=False,
        )
        self.assertEqual(User.objects.count(), 0)

    def test_adult_invalid_signup_rerenders_form(self):
        """An adult year with other missing fields is a normal form error
        (200 re-render), not a guardian redirect."""
        response = self.client.post(reverse('account_signup'), {
            'name': 'Adult Reader',
            'birth_year': str(timezone.now().year - 30),
            'accept_terms': 'on',
        })
        self.assertEqual(response.status_code, 200)

    def test_guardian_page_renders(self):
        """The explainer page is directly linkable and serves 200."""
        response = self.client.get(reverse('guardian_account_info'))
        self.assertEqual(response.status_code, 200)
        # Stable markers rather than the (often reworded) explainer copy:
        # the age badge, the DPDP citation and the guardian sign-up link.
        self.assertContains(response, '<span class="status-text">18+</span>', html=True)
        self.assertContains(response, "DPDP 2023")
        self.assertContains(response, f'href="{reverse("account_signup")}"')


class MockPaymentGateTests(TestCase):
    """
    /reader/process-payment/ records a "completed" MockGateway payment and
    activates the plan without taking money. It must be unreachable unless
    MOCK_PAYMENTS_ENABLED is on, and even then only accept a checkout the
    server itself opened, once.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            phone_number="+917700770078", name="Lucy", password="lucypassword"
        )
        self.client.force_login(self.user)
        self.checkout_url = reverse("reader_checkout", args=["1_month"])
        self.pay_url = reverse("process_payment")

    def _open_checkout(self, plan="1_month"):
        response = self.client.get(reverse("reader_checkout", args=[plan]))
        self.assertEqual(response.status_code, 200)
        return response.context["idempotency_key"]

    def _pay(self, key, plan="1_month"):
        return self.client.post(self.pay_url, {
            "plan_type": plan, "payment_method": "upi", "idempotency_key": key,
        })

    def test_disabled_by_default(self):
        response = self._pay("IK_anything")
        self.assertEqual(response.status_code, 404)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_subscribed)
        self.assertFalse(PaymentDetails.objects.exists())

    def test_checkout_requires_login(self):
        self.client.logout()
        response = self.client.get(self.checkout_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_checkout_page_has_no_payment_form_when_disabled(self):
        response = self.client.get(self.checkout_url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.pay_url)

    def test_production_settings_force_it_off(self):
        import os
        import subprocess
        import sys

        from django.conf import settings

        env = {
            "PATH": os.environ.get("PATH", ""), "ICARUS_DOTENV": "",
            "SECRET_KEY": "x", "SALT_KEY": "y", "PHONE_HASH_KEY": "z",
            "MOCK_PAYMENTS_ENABLED": "True",
        }
        out = subprocess.run(
            [sys.executable, "-c",
             "import Icarus.settings.production as p; print(p.MOCK_PAYMENTS_ENABLED, p.DEBUG)"],
            cwd=settings.BASE_DIR, env=env, capture_output=True, text=True,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.split(), ["False", "False"])

    @override_settings(MOCK_PAYMENTS_ENABLED=True)
    def test_payment_activates_plan_once(self):
        key = self._open_checkout()

        self.assertEqual(self._pay(key).status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_subscribed)
        self.assertEqual(PaymentDetails.objects.filter(idempotency_key=key).count(), 1)

        # Double submit: no second payment, plan not re-applied.
        self.user.subscription_plan = "none"
        self.user.subscription_end = None
        self.user.save()
        self.assertEqual(self._pay(key).status_code, 302)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_subscribed)
        self.assertEqual(PaymentDetails.objects.count(), 1)

    @override_settings(MOCK_PAYMENTS_ENABLED=True)
    def test_missing_or_invented_key_is_rejected(self):
        self._open_checkout()
        for key in ("", "IK_made_up_by_the_client"):
            response = self._pay(key)
            self.assertRedirects(response, self.checkout_url, fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_subscribed)
        self.assertFalse(PaymentDetails.objects.exists())

    @override_settings(MOCK_PAYMENTS_ENABLED=True)
    def test_key_only_buys_the_plan_it_was_issued_for(self):
        key = self._open_checkout("1_month")
        self._pay(key, plan="5_years")
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_subscribed)
        self.assertFalse(PaymentDetails.objects.exists())

    @override_settings(MOCK_PAYMENTS_ENABLED=True)
    def test_another_users_key_is_rejected(self):
        key = self._open_checkout()
        other = User.objects.create_user(phone_number="+917700770079", name="Linus")
        self.client.force_login(other)  # new session: the key was never issued to it
        self._pay(key)
        other.refresh_from_db()
        self.assertFalse(other.is_subscribed)
        self.assertFalse(PaymentDetails.objects.exists())


class AdminPurgeTriggerTests(TestCase):
    """
    The anonymise / 8-year purge buttons used to run on a plain GET and
    redirect to whatever Referer said. Now: GET only shows a confirmation,
    POST queues the command, and the redirect is a fixed admin page.
    """

    def setUp(self):
        from unittest.mock import patch

        self.admin = User.objects.create_superuser(phone_number="+917700110001", name="Admin", password="x")
        self.client.force_login(self.admin)
        patcher = patch("django_q.tasks.async_task")
        self.async_task = patcher.start()
        self.addCleanup(patcher.stop)

    def test_get_only_asks_for_confirmation(self):
        for name in ("admin_trigger_purge_deactivated", "admin_trigger_purge_expired"):
            with self.subTest(name=name):
                response = self.client.get(reverse(name), HTTP_REFERER="https://evil.example/")
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "<form method=\"post\">")
        self.async_task.assert_not_called()

    def test_post_queues_the_command_and_returns_to_a_fixed_page(self):
        cases = (
            ("admin_trigger_purge_deactivated", "purge_deactivated_users", "wagtailsnippets_reader_readeruser:list"),
            ("admin_trigger_purge_expired", "purge_expired_data", "wagtailsnippets_reader_subscriptionhistory:list"),
        )
        for name, command, list_name in cases:
            with self.subTest(name=name):
                self.async_task.reset_mock()
                response = self.client.post(reverse(name), HTTP_REFERER="https://evil.example/")
                self.assertRedirects(response, reverse(list_name), fetch_redirect_response=False)
                self.async_task.assert_called_once_with("django.core.management.call_command", command)

    def test_non_superusers_are_refused(self):
        staff = User.objects.create_user(phone_number="+917700110002", name="Staff", is_staff=True)
        self.client.force_login(staff)
        # Wagtail sends users without admin access to its login page; staff
        # who do have it get the superuser check's 403.
        self.assertIn(self.client.post(reverse("admin_trigger_purge_expired")).status_code, (302, 403))
        self.async_task.assert_not_called()


class WagtailUserCreationFormTests(TestCase):
    """
    Admin > Users > Add user could never succeed: first_name/last_name were
    required but hidden, and there was no way to enter the phone number
    that is the account's login identifier.
    """

    def setUp(self):
        self.admin = User.objects.create_superuser(phone_number="+917700120001", name="Admin", password="x")
        self.client.force_login(self.admin)

    def _post(self, **overrides):
        data = {
            "phone_number_0": "IN", "phone_number_1": "9876500001",
            "name": "Asha Kumari", "email": "",
            "password1": "Long-enough-pass-91", "password2": "Long-enough-pass-91",
            "is_active": "on",
        }
        data.update(overrides)
        return self.client.post(reverse("wagtailusers_users:add"), data)

    def test_admin_can_create_a_reader_who_can_log_in_by_phone(self):
        response = self._post()
        self.assertEqual(response.status_code, 302)

        user = User.objects.get(phone_number_hash=User.hash_phone("+919876500001"))
        self.assertEqual(user.phone_number_encrypted, "+919876500001")
        self.assertEqual(user.name, "Asha Kumari")
        self.assertEqual((user.first_name, user.last_name), ("Asha", "Kumari"))
        self.assertIsNone(user.email)
        self.assertTrue(user.check_password("Long-enough-pass-91"))

    def test_phone_number_must_be_unique(self):
        User.objects.create_user(phone_number="+919876500002", name="Existing")
        response = self._post(phone_number_1="9876500002")
        self.assertEqual(response.status_code, 200)
        self.assertIn("phone_number", response.context["form"].errors)

    def test_create_page_shows_the_phone_field(self):
        response = self.client.get(reverse("wagtailusers_users:add"))
        self.assertContains(response, 'name="phone_number_1"')


class AuditLogPrivacyTests(TestCase):
    """
    The audit log is readable by every holder of home.view_auditlog. It
    used to receive readers' decrypted phone numbers, addresses, IPs and
    password hashes on every save.
    """

    def setUp(self):
        from auditlog import get_logentry_model
        from django.contrib.contenttypes.models import ContentType

        self.LogEntry = get_logentry_model()
        self.ct = ContentType.objects.get_for_model(User)
        self.user = User.objects.create_user(
            phone_number="+917700130001", name="Priya", email="priya@example.com", password="pw-12345678"
        )

    def _latest_changes(self):
        entry = self.LogEntry.objects.filter(content_type=self.ct, object_id=self.user.pk).order_by("-pk").first()
        return entry.changes if isinstance(entry.changes, dict) else json.loads(entry.changes or "{}")

    def test_personal_fields_never_reach_the_log(self):
        from reader.models import READER_AUDIT_EXCLUDED_FIELDS

        self.user.set_phone("+917700130002")
        self.user.email = "new@example.com"
        self.user.address_line_1 = "12 Secret Lane"
        self.user.registration_ip = "203.0.113.9"
        self.user.set_password("another-password-1")
        self.user.name = "Priya K"
        self.user.save()

        changes = self._latest_changes()
        self.assertIn("name", changes)  # ordinary changes are still audited
        for field in READER_AUDIT_EXCLUDED_FIELDS:
            self.assertNotIn(field, changes)
        everything = json.dumps(list(self.LogEntry.objects.filter(content_type=self.ct).values()), default=str)
        for secret in ("+917700130001", "+917700130002", "12 Secret Lane", "203.0.113.9", "pbkdf2", "md5$"):
            self.assertNotIn(secret, everything)

    def test_str_never_shows_the_phone_number(self):
        no_email = User.objects.create_user(phone_number="+917700130003", name="Ravi")
        self.assertEqual(str(no_email), "Ravi")
        self.assertEqual(str(self.user), "Priya (priya@example.com)")


class ErasureTests(TestCase):
    """DPDP erasure (anonymize_account) and the purge commands."""

    def setUp(self):
        from auditlog import get_logentry_model
        from django.contrib.contenttypes.models import ContentType

        self.LogEntry = get_logentry_model()
        self.user_ct = ContentType.objects.get_for_model(User)
        self.user = User.objects.create_user(phone_number="+917700140001", name="Leaving Reader", email="l@example.com")
        self.user.name = "Leaving Reader 2"
        self.user.save()

    def test_rows_about_the_reader_are_deleted(self):
        self.assertTrue(self.LogEntry.objects.filter(content_type=self.user_ct, object_id=self.user.pk).exists())
        self.user.anonymize_account()
        self.assertFalse(self.LogEntry.objects.filter(content_type=self.user_ct, object_id=self.user.pk).exists())
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_anonymized)

    def test_rows_by_the_reader_are_kept_without_their_identity(self):
        from auditlog.context import set_actor

        other = User.objects.create_user(phone_number="+917700140002", name="Someone else")
        with set_actor(self.user, remote_addr="198.51.100.4"):
            other.name = "Someone else, renamed"
            other.save()
        row = self.LogEntry.objects.filter(content_type=self.user_ct, object_id=other.pk).order_by("-pk").first()
        self.assertEqual(row.actor_id, self.user.pk)

        self.user.anonymize_account()

        row.refresh_from_db()  # still there: it is the other reader's history
        self.assertIsNone(row.actor_id)
        self.assertIsNone(row.remote_addr)

    def test_failure_leaves_the_account_unchanged(self):
        from unittest.mock import patch

        with patch.object(self.LogEntry.objects, "filter", side_effect=RuntimeError("db down")):
            with self.assertRaises(RuntimeError):
                self.user.anonymize_account()
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_anonymized)

    def test_purge_continues_past_a_failing_account(self):
        from datetime import timedelta
        from unittest.mock import patch

        from reader.models import ReaderUser

        long_ago = timezone.now() - timedelta(days=400)
        second = User.objects.create_user(phone_number="+917700140003", name="Second")
        User.objects.filter(pk__in=[self.user.pk, second.pk]).update(is_active=False, deactivated_at=long_ago)

        real = ReaderUser.anonymize_account
        calls = []

        def flaky(self_):
            calls.append(self_.pk)
            if len(calls) == 1:
                raise RuntimeError("boom")
            return real(self_)

        err = StringIO()
        with patch.object(ReaderUser, "anonymize_account", flaky):
            call_command("purge_deactivated_users", stdout=StringIO(), stderr=err)

        self.assertEqual(len(calls), 2)
        self.assertEqual(User.objects.filter(is_anonymized=True).count(), 1)
        self.assertIn("1 account(s) failed", err.getvalue())


class AuditScrubMigrationTests(TestCase):
    def test_existing_rows_lose_personal_data(self):
        import importlib

        from auditlog import get_logentry_model
        from django.apps import apps
        from django.contrib.contenttypes.models import ContentType

        LogEntry = get_logentry_model()
        ct = ContentType.objects.get_for_model(User)
        user = User.objects.create_user(phone_number="+917700150001", name="Old Row")
        LogEntry.objects.all().delete()
        kept = LogEntry.objects.create(
            content_type=ct, object_id=user.pk, object_pk=str(user.pk), action=1,
            object_repr="Old Row (+917700150001)",
            changes={"name": ["A", "B"], "phone_number_encrypted": ["+917700150001", "+917700150009"],
                     "address_line_1": ["x", "y"]},
        )
        only_pii = LogEntry.objects.create(
            content_type=ct, object_id=user.pk, object_pk=str(user.pk), action=1,
            object_repr="Old Row", changes={"password": ["a", "b"]},
        )

        importlib.import_module("home.migrations.0018_scrub_personal_data_from_audit_log").scrub(apps, None)

        kept.refresh_from_db()
        self.assertEqual(kept.changes, {"name": ["A", "B"]})
        self.assertEqual(kept.object_repr, "Old Row ([phone removed])")
        self.assertFalse(LogEntry.objects.filter(pk=only_pii.pk).exists())


class MeterMigrationTests(TestCase):
    def test_existing_reads_are_copied_to_the_meter(self):
        import importlib

        from django.apps import apps

        root = Page.get_first_root_node()
        art = Article(title="Read before", slug="read-before", date=date(2026, 1, 1))
        root.add_child(instance=art)
        user = User.objects.create_user(phone_number="+917700160001", name="Old Reader")
        user.read_articles.add(art)

        importlib.import_module("reader.migrations.0013_readeruser_metered_articles").copy_reads_into_meter(apps, None)

        self.assertEqual(list(user.metered_articles.all()), [art])


class LoginLoggingTests(TestCase):
    """Failed-login tracking printed the submitted phone/email and names to stdout."""

    def test_failed_login_logs_only_the_account_id(self):
        from django.contrib.auth import authenticate

        user = User.objects.create_user(phone_number="+917700170001", name="Secret Name", password="right-password")
        with self.assertLogs("reader.models", level="INFO") as logs:
            authenticate(phone_number="+917700170001", password="wrong", username="+917700170001")
        text = "\n".join(logs.output)
        self.assertIn(str(user.pk), text)
        self.assertNotIn("+917700170001", text)
        self.assertNotIn("Secret Name", text)


class MailingListCsvTests(TestCase):
    def test_formula_like_cells_are_neutralised(self):
        admin = User.objects.create_superuser(phone_number="+917700180001", name="Admin", password="x")
        User.objects.create_user(
            phone_number="+917700180002", name='=HYPERLINK("http://evil.example","x")',
            is_print_subscriber=True, print_delivery_status="active",
            print_expiry_date=timezone.now().date() + timezone.timedelta(days=30),
            address_line_1="@SUM(1+1)", city="-2+3",
        )
        self.client.force_login(admin)
        from reader.views import export_mailing_list
        request = RequestFactory().get("/")
        request.user = admin
        rows = list(csv.reader(StringIO(export_mailing_list(request).content.decode())))
        name, phone, line1 = rows[1][0], rows[1][1], rows[1][2]
        self.assertTrue(name.startswith("'="))
        self.assertTrue(line1.startswith("'@"))
        self.assertEqual(phone, "+917700180002")  # a validated number is left alone


class RetentionScheduleTests(TestCase):
    def test_schedules_are_created_once(self):
        from django_q.models import Schedule

        call_command("setup_retention_schedules", stdout=StringIO())
        call_command("setup_retention_schedules", stdout=StringIO())
        names = set(Schedule.objects.values_list("func", "args"))
        self.assertEqual(Schedule.objects.count(), 2)
        self.assertEqual(names, {
            ("django.core.management.call_command", "'purge_deactivated_users'"),
            ("django.core.management.call_command", "'purge_expired_data'"),
        })


class ReaderAppConfigTests(TestCase):
    """
    reader/apps.py exposes several AppConfig classes, so a bare "reader" in
    INSTALLED_APPS made Django fall back to a plain AppConfig: ready() never
    ran, the newsletter receiver was never connected and PaymentDetails was
    never audited.
    """

    def test_reader_config_is_loaded(self):
        from django.apps import apps

        from reader.apps import ReaderConfig

        self.assertIsInstance(apps.get_app_config("reader"), ReaderConfig)

    def test_ready_ran(self):
        # (That the newsletter receiver is connected is proven by
        # NewIssueNewsletterTests.test_publish_queues_the_task_after_commit.)
        from auditlog.registry import auditlog

        self.assertTrue(auditlog.contains(PaymentDetails))
        # ReaderUser keeps the models.py registration with its exclusions.
        self.assertTrue(auditlog.get_model_fields(ReaderUser)["exclude_fields"])


class NewIssueNewsletterTests(TestCase):
    """
    The new-issue newsletter: queued after publish commits, sent once per
    issue however often it is re-published, rendered from escaped templates,
    with a working one-click unsubscribe.
    """

    def setUp(self):
        Site.objects.all().delete()
        for child in Page.get_first_root_node().get_children():
            child.delete()
        root = Page.get_first_root_node()
        home = HomePage(title="Home", slug="home")
        root.add_child(instance=home)
        Site.objects.create(hostname="testserver", root_page=home, is_default_site=True)
        index = IssueIndexPage(title="Issues", slug="issues")
        home.add_child(instance=index)
        self.issue = Issue(
            title="Issue <One>", slug="issue-one", volume=Volume.objects.create(number=1, year_start=2026),
            issue_number=1, date_of_publishing=date(2026, 1, 1), live=False,
        )
        index.add_child(instance=self.issue)

        self.subscriber = User.objects.create_user(
            phone_number="+917700190001", name="<b>Bold</b> Reader", email="sub@example.com",
        )
        self.subscriber.newsletter_opt_in = True
        self.subscriber.save()
        # Not recipients: opted out, and opted in without an email address.
        User.objects.create_user(phone_number="+917700190002", name="Quiet", email="quiet@example.com")
        no_email = User.objects.create_user(phone_number="+917700190003", name="No Email")
        no_email.newsletter_opt_in = True
        no_email.save()

    def _publish(self):
        from unittest.mock import patch

        with patch("django_q.tasks.async_task") as async_task:
            with self.captureOnCommitCallbacks(execute=True):
                self.issue.save_revision().publish()
        return async_task

    def test_publish_queues_the_task_after_commit(self):
        async_task = self._publish()
        async_task.assert_called_once_with("reader.tasks.send_issue_newsletter_task", self.issue.pk)

    def test_republishing_a_sent_issue_queues_nothing(self):
        Issue.objects.filter(pk=self.issue.pk).update(newsletter_sent_at=timezone.now())
        self.issue.refresh_from_db()
        self._publish().assert_not_called()

    def test_task_sends_once_however_often_it_runs(self):
        from django.core import mail

        from reader.tasks import send_issue_newsletter_task

        self.assertEqual(send_issue_newsletter_task(self.issue.pk), "sent=1 failed=0")
        self.assertEqual(send_issue_newsletter_task(self.issue.pk), "skipped")
        self.assertEqual([m.to for m in mail.outbox], [["sub@example.com"]])
        self.issue.refresh_from_db()
        self.assertIsNotNone(self.issue.newsletter_sent_at)

    def test_claim_is_released_if_sending_cannot_start(self):
        from unittest.mock import patch

        from reader.tasks import send_issue_newsletter_task

        with patch("reader.newsletter.get_connection", side_effect=ConnectionRefusedError):
            with self.assertRaises(ConnectionRefusedError):
                send_issue_newsletter_task(self.issue.pk)
        self.issue.refresh_from_db()
        self.assertIsNone(self.issue.newsletter_sent_at)  # django-q may retry

    def test_email_is_escaped_branded_and_unsubscribable(self):
        from django.conf import settings
        from django.core import mail

        from reader.newsletter import send_new_issue_newsletter

        send_new_issue_newsletter(self.issue)
        msg = mail.outbox[0]
        html = msg.alternatives[0][0]

        self.assertIn(settings.WAGTAIL_SITE_NAME, msg.subject)
        self.assertNotIn("ഇക്കാരസ്", msg.subject)
        self.assertNotIn("<b>Bold</b>", html)
        self.assertIn("&lt;b&gt;Bold&lt;/b&gt;", html)
        self.assertIn("Issue &lt;One&gt;", html)
        self.assertEqual(msg.extra_headers["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")
        url = msg.extra_headers["List-Unsubscribe"].strip("<>")
        self.assertIn(url, msg.body)

        path = url.split("testserver", 1)[1]
        # GET only asks (link scanners must not unsubscribe anyone)...
        self.assertEqual(self.client.get(path).status_code, 200)
        self.subscriber.refresh_from_db()
        self.assertTrue(self.subscriber.newsletter_opt_in)
        # ...the one-click POST unsubscribes, without login or CSRF token.
        csrf_client = self.client_class(enforce_csrf_checks=True)
        self.assertEqual(csrf_client.post(path).status_code, 200)
        self.subscriber.refresh_from_db()
        self.assertFalse(self.subscriber.newsletter_opt_in)

    def test_tampered_unsubscribe_token_is_404(self):
        from reader.newsletter import unsubscribe_token

        token = unsubscribe_token(self.subscriber)
        response = self.client.post(reverse("newsletter_unsubscribe", args=[token[:-2] + "xx"]))
        self.assertEqual(response.status_code, 404)

    def test_migration_marks_already_published_issues_as_sent(self):
        import importlib

        from django.apps import apps

        self.issue.save_revision().publish()
        Issue.objects.filter(pk=self.issue.pk).update(newsletter_sent_at=None)

        importlib.import_module("issue.migrations.0010_issue_newsletter_sent_at").mark_published_issues_as_sent(apps, None)

        self.issue.refresh_from_db()
        self.assertEqual(self.issue.newsletter_sent_at, self.issue.first_published_at)


class ReaderSaveFastPathTests(TestCase):
    """
    save(update_fields=[...]) for fields the bookkeeping doesn't watch
    (last_login on every login, last_login_ip) skips the extra SELECT and the
    subscription-history logic, which used to run on unsaved in-memory values.
    """

    def test_unrelated_update_writes_no_history_from_unsaved_values(self):
        user = User.objects.create_user(phone_number="+917700200001", name="Fast")
        user.subscription_plan = "1_year"  # changed in memory only, never saved
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
        self.assertFalse(SubscriptionHistory.objects.filter(reader=user).exists())
        user.refresh_from_db()
        self.assertEqual(user.subscription_plan, "none")

    def test_unrelated_update_is_cheap(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        user = User.objects.create_user(phone_number="+917700200002", name="Fast 2")
        user.last_login_ip = "198.51.100.1"
        with CaptureQueriesContext(connection) as ctx:
            user.save(update_fields=["last_login_ip"])
        # Before the UPDATE only auditlog's own lookup of the previous row
        # remains (save() used to add its own SELECT of the row). Later
        # queries are Wagtail's post-save search/reference indexing,
        # unrelated to this change.
        sql = [q["sql"] for q in ctx.captured_queries]
        update_at = next(i for i, q in enumerate(sql) if q.startswith('UPDATE "reader_readeruser"'))
        selects_before = [q for q in sql[:update_at] if q.startswith("SELECT")]
        self.assertEqual(len(selects_before), 1)

    def test_watched_fields_still_run_the_bookkeeping(self):
        user = User.objects.create_user(phone_number="+917700200003", name="Watched")
        user.subscription_plan = "1_month"
        user.save(update_fields=["subscription_plan", "subscription_start", "subscription_end"])
        self.assertTrue(SubscriptionHistory.objects.filter(reader=user, is_active=True).exists())
