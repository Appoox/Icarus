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
        self.user = User.objects.create_user(username='testuser', password='password')
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
        expected_str = f"Bug Report by {self.user} — {self.postbox.submitted_at:%Y-%m-%d %H:%M}"
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
        self.user = User.objects.create_user(username='regularuser', password='password')
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
        self.client.login(username='regularuser', password='password')
        response = self.client.get(self.url, {'from': '/about-us/'})
        
        # Verify page loads successfully and context contains the 'from' parameter
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.context['form'], PostboxForm)
        self.assertEqual(response.context['form'].initial.get('page_context'), '/about-us/')

    def test_postbox_page_post(self):
        # Log in the user and submit feedback
        self.client.login(username='regularuser', password='password')
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
    Test suite for the Wagtail admin views to ensure staff-only access,
    filtering, and note-updating functionality.
    """
    def setUp(self):
        self.client = Client()
        
        # Create a regular user and a staff user
        self.regular_user = User.objects.create_user(username='user', password='password')
        self.staff_user = User.objects.create_user(username='staff', password='password', is_staff=True)
        
        # Create a couple of test submissions
        self.sub1 = Postbox.objects.create(
            feedback_type='bug', feedback='Bug 1', user=self.regular_user, is_reviewed=False
        )
        self.sub2 = Postbox.objects.create(
            feedback_type='suggestion', feedback='Suggestion 1', user=self.regular_user, is_reviewed=True
        )

        self.index_url = reverse('icarus_postbox_index')
        self.detail_url = reverse('icarus_postbox_detail', args=[self.sub1.pk])

    def test_admin_access_forbidden_for_regular_users(self):
        # Attempt to access admin views as a non-staff user
        self.client.login(username='user', password='password')
        
        # Should return HTTP 403 Forbidden
        response_index = self.client.get(self.index_url)
        self.assertEqual(response_index.status_code, 403)
        
        response_detail = self.client.get(self.detail_url)
        self.assertEqual(response_detail.status_code, 403)

    def test_admin_index_get_and_filter(self):
        # Access the index view as a staff member
        self.client.login(username='staff', password='password')
        
        # Test basic GET
        response = self.client.get(self.index_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['page_obj']), 2)
        
        # Test filtering by feedback_type
        response_filtered = self.client.get(self.index_url, {'type': 'bug'})
        self.assertEqual(response_filtered.status_code, 200)
        self.assertEqual(len(response_filtered.context['page_obj']), 1)
        self.assertEqual(response_filtered.context['page_obj'][0], self.sub1)

        # Test filtering by review status
        response_unreviewed = self.client.get(self.index_url, {'reviewed': '0'})
        self.assertEqual(len(response_unreviewed.context['page_obj']), 1)
        self.assertEqual(response_unreviewed.context['page_obj'][0], self.sub1)

    def test_admin_detail_post(self):
        # Test updating admin notes and review status via POST in the detail view
        self.client.login(username='staff', password='password')
        post_data = {
            'admin_notes': 'Will fix in next sprint.',
            'is_reviewed': '1'
        }
        
        response = self.client.post(self.detail_url, data=post_data)
        
        # Verify successful update redirects back to the detail page
        self.assertRedirects(response, self.detail_url)
        
        # Refresh object from DB to check if updates were saved
        self.sub1.refresh_from_db()
        self.assertEqual(self.sub1.admin_notes, 'Will fix in next sprint.')
        self.assertTrue(self.sub1.is_reviewed)