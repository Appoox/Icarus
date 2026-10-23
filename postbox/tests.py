from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from .models import Postbox
from .forms import PostboxForm

# Get the active User model for the project
User = get_user_model()


class PostboxModelTests(TestCase):
    """
    Test suite for the Postbox model to ensure fields and string 
    representations function as expected.
    """
    def setUp(self):
        # Create a test user for the foreign key relationship
        self.user = User.objects.create_user(phone_number='+919000000001', name='Test User', password='password')
        # Create a basic Postbox submission
        self.postbox = Postbox.objects.create(
            feedback_type='bug',
            feedback='The submit button is broken.',
            rating=4,
            user=self.user
        )

    def test_postbox_creation(self):
        # Verify the object was created and saved to the database
        self.assertEqual(Postbox.objects.count(), 1)
        self.assertEqual(self.postbox.feedback, 'The submit button is broken.')
        self.assertFalse(self.postbox.is_reviewed)

    def test_postbox_str_representation(self):
        # Verify the __str__ method returns the expected human-readable format
        expected_str = (
            f"{self.postbox.get_feedback_type_display()} by {self.user} "
            f"— {self.postbox.submitted_at:%Y-%m-%d %H:%M}"
        )
        self.assertEqual(str(self.postbox), expected_str)


class PostboxFormTests(TestCase):
    """
    Test suite for the PostboxForm to ensure validation logic 
    handles valid and invalid inputs correctly.
    """
    def test_valid_form(self):
        # Provide valid data to the form
        form_data = {
            'feedback_type': 'suggestion',
            'feedback': 'Add a dark mode feature.',
            'rating': 5,
            'page_context': '/home/'
        }
        form = PostboxForm(data=form_data)
        # Form should be valid
        self.assertTrue(form.is_valid())

    def test_invalid_form_missing_feedback(self):
        # Omit the required 'feedback' field
        form_data = {
            'feedback_type': 'general',
            'rating': 3
        }
        form = PostboxForm(data=form_data)
        # Form should be invalid and contain an error for 'feedback'
        self.assertFalse(form.is_valid())
        self.assertIn('feedback', form.errors)


class PostboxViewTests(TestCase):
    """
    Test suite for the public-facing Postbox view.
    Ensures authentication requirements and submission logic work.
    """
    def setUp(self):
        # Initialize test client and user
        self.client = Client()
        self.user = User.objects.create_user(phone_number='+919000000002', name='Regular User', password='password')
        # Note: Assuming the URL namespace/name based on the redirect in views.py
        # You may need to adjust 'postbox:page' if your urls.py names it differently.
        try:
            self.url = reverse('postbox:page')
        except:
            # Fallback for testing purposes if namespace isn't set up in the test environment
            self.url = '/postbox/' 

    def test_postbox_page_requires_login(self):
        # Attempt to access the page without logging in
        response = self.client.get(self.url)
        # Should redirect to the login page (HTTP 302)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/accounts/login/'))

    def test_postbox_page_get(self):
        # Log in the user and access the page
        self.client.force_login(self.user)
        response = self.client.get(self.url, {'from': '/about-us/'})
        
        # Verify page loads successfully and context contains the 'from' parameter
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.context['form'], PostboxForm)
        self.assertEqual(response.context['form'].initial.get('page_context'), '/about-us/')

    def test_postbox_page_post(self):
        # Log in the user and submit feedback
        self.client.force_login(self.user)
        post_data = {
            'feedback_type': 'content',
            'feedback': 'Typo on the about page.',
            'rating': 2,
            'page_context': '/about-us/'
        }
        response = self.client.post(self.url, data=post_data)
        
        # Verify successful submission redirects back to the page
        self.assertRedirects(response, self.url)
        
        # Verify the submission is saved in the database and tied to the user
        self.assertEqual(Postbox.objects.count(), 1)
        submission = Postbox.objects.first()
        self.assertEqual(submission.user, self.user)
        self.assertEqual(submission.feedback, 'Typo on the about page.')


class PostboxAdminViewTests(TestCase):
    """
    The Wagtail snippet viewset for Postbox: staff-only access, filtering,
    and saving review notes.
    """
    def setUp(self):
        from django.contrib.auth.models import Permission

        self.client = Client()
        self.regular_user = User.objects.create_user(phone_number='+919000000003', name='User', password='password')
        self.staff_user = User.objects.create_user(phone_number='+919000000004', name='Staff', password='password', is_staff=True)
        self.staff_user.user_permissions.add(*Permission.objects.filter(
            codename__in=['access_admin', 'view_postbox', 'change_postbox'],
        ))

        self.sub1 = Postbox.objects.create(
            feedback_type='bug', feedback='Bug 1', user=self.regular_user, is_reviewed=False
        )
        self.sub2 = Postbox.objects.create(
            feedback_type='suggestion', feedback='Suggestion 1', user=self.regular_user, is_reviewed=True
        )

        self.index_url = reverse('wagtailsnippets_postbox_postbox:list')
        self.edit_url = reverse('wagtailsnippets_postbox_postbox:edit', args=[self.sub1.pk])

    def test_admin_access_forbidden_for_regular_users(self):
        self.client.force_login(self.regular_user)
        for url in (self.index_url, self.edit_url):
            response = self.client.get(url)
            # Wagtail bounces users without admin access to its login page.
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.url.startswith(reverse('wagtailadmin_login')))

    def test_admin_index_get_and_filter(self):
        self.client.force_login(self.staff_user)

        response = self.client.get(self.index_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['object_list']), 2)

        response = self.client.get(self.index_url, {'feedback_type': 'bug'})
        self.assertEqual(list(response.context['object_list']), [self.sub1])

        response = self.client.get(self.index_url, {'is_reviewed': 'False'})
        self.assertEqual(list(response.context['object_list']), [self.sub1])

    def test_admin_edit_saves_review(self):
        self.client.force_login(self.staff_user)
        response = self.client.post(self.edit_url, {
            'admin_notes': 'Will fix in next sprint.',
            'is_reviewed': 'on',
        })
        self.assertEqual(response.status_code, 302)

        self.sub1.refresh_from_db()
        self.assertEqual(self.sub1.admin_notes, 'Will fix in next sprint.')
        self.assertTrue(self.sub1.is_reviewed)
        # Read-only fields are untouched by the edit form.
        self.assertEqual(self.sub1.feedback, 'Bug 1')


class PostboxNotificationToggleTests(TestCase):
    """The mute/unmute toggle changes state, so it only answers POST."""

    def test_get_is_refused_and_post_toggles(self):
        from django.contrib.auth.models import Permission

        from .models import PostboxNotificationPreference

        staff = User.objects.create_user(phone_number='+919000000010', name='Staff', is_staff=True)
        staff.user_permissions.add(Permission.objects.get(codename='access_admin'))
        self.client.force_login(staff)
        url = reverse('toggle_postbox_notifications')

        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertFalse(PostboxNotificationPreference.objects.filter(user=staff).exists())

        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertFalse(PostboxNotificationPreference.objects.get(user=staff).receive_notifications)
