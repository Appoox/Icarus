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
        self.assertContains(response, "അക്കൗണ്ട് എടുക്കാൻ 18 വയസ്സ് തികഞ്ഞിരിക്കണം")
