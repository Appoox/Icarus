from datetime import date
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.core import mail
from wagtail.models import Page, Site
from articles.models import Article
from home.models import HomePage
from .models import Comment, CommentReport, CommentNotificationPreference, DashboardNotification, CommentSettings

User = get_user_model()

class CommentSystemTests(TestCase):
    def setUp(self):
        # Remove Wagtail's default "Welcome" page (slug "home") so ours can
        # take its slug, then re-fetch the root so treebeard's numchild is fresh.
        Site.objects.all().delete()
        for child in Page.get_first_root_node().get_children():
            child.delete()
        self.root_page = Page.get_first_root_node()
        self.home_page = HomePage(title="Home", slug="home")
        self.root_page.add_child(instance=self.home_page)
        
        # Create article page
        self.article_page = Article(title="Test Article", slug="test-article", date=date(2026, 1, 1))
        self.home_page.add_child(instance=self.article_page)

        # Setup site (required for Site.find_for_request)
        self.site = Site.objects.create(
            hostname='localhost',
            port=80,
            root_page=self.home_page,
            is_default_site=True
        )

        # Create Users
        self.reader = User.objects.create_user(
            phone_number="+15555555555",
            name="Test Reader",
            password="readerpassword",
            email="reader@test.com"
        )
        self.restricted_reader = User.objects.create_user(
            phone_number="+15555555556",
            name="Restricted Reader",
            password="restrictedpassword",
            email="restricted@test.com",
            can_comment=False
        )
        self.staff_1 = User.objects.create_superuser(
            phone_number="+15555555557",
            name="Staff One",
            password="staffpassword",
            email="staff1@test.com"
        )
        self.staff_2 = User.objects.create_superuser(
            phone_number="+15555555558",
            name="Staff Two",
            password="staffpassword",
            email="staff2@test.com"
        )
        # Opt-out staff_2 from comment notifications
        CommentNotificationPreference.objects.create(
            user=self.staff_2,
            receive_notifications=False
        )

        # Setup comment settings
        self.comment_settings = CommentSettings.for_site(self.site)
        self.comment_settings.global_premoderation = False
        self.comment_settings.save()

    def test_post_comment_authenticated(self):
        self.client.force_login(self.reader)
        url = reverse('kalapila:post_comment')
        
        response = self.client.post(url, {
            'page_id': self.article_page.id,
            'body': 'This is a test comment.'
        })
        
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content.decode(), {
            'status': 'success',
            'html': response.json()['html'],
            'comment_id': response.json()['comment_id'],
            'parent_id': None
        })
        
        self.assertTrue(Comment.objects.filter(body='This is a test comment.').exists())

    def test_post_comment_anonymous_rejected(self):
        url = reverse('kalapila:post_comment')
        response = self.client.post(url, {
            'page_id': self.article_page.id,
            'body': 'This is an anonymous comment.'
        })
        self.assertEqual(response.status_code, 302) # Redirect to login

    def test_post_comment_restricted_user_rejected(self):
        self.client.force_login(self.restricted_reader)
        url = reverse('kalapila:post_comment')
        response = self.client.post(url, {
            'page_id': self.article_page.id,
            'body': 'Should fail.'
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['status'], 'error')

    def test_post_comment_body_limit(self):
        self.client.force_login(self.reader)
        url = reverse('kalapila:post_comment')
        long_body = 'a' * 3001
        response = self.client.post(url, {
            'page_id': self.article_page.id,
            'body': long_body
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['status'], 'error')

    def test_post_comment_replies(self):
        self.client.force_login(self.reader)
        # Pre-create parent comment
        parent = Comment.objects.create(
            page=self.article_page,
            user=self.reader,
            body="Parent comment"
        )
        url = reverse('kalapila:post_comment')
        response = self.client.post(url, {
            'page_id': self.article_page.id,
            'parent_id': parent.id,
            'body': 'This is a reply.'
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['parent_id'], str(parent.id))
        
        reply = Comment.objects.get(id=response.json()['comment_id'])
        self.assertEqual(reply.parent, parent)

    def test_premoderation_global_setting(self):
        # Enable global premoderation
        self.comment_settings.global_premoderation = True
        self.comment_settings.save()

        self.client.force_login(self.reader)
        url = reverse('kalapila:post_comment')
        response = self.client.post(url, {
            'page_id': self.article_page.id,
            'body': 'Awaiting moderation.'
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'pending_approval')
        
        comment = Comment.objects.get(body='Awaiting moderation.')
        self.assertFalse(comment.is_approved)

    def test_premoderation_per_page_setting(self):
        # Premoderate this article page specifically
        self.comment_settings.premoderated_pages.add(self.article_page)
        self.comment_settings.save()

        self.client.force_login(self.reader)
        url = reverse('kalapila:post_comment')
        response = self.client.post(url, {
            'page_id': self.article_page.id,
            'body': 'Specific moderation.'
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'pending_approval')
        
        comment = Comment.objects.get(body='Specific moderation.')
        self.assertFalse(comment.is_approved)

    def test_staff_notifications(self):
        # Clear outbox
        mail.outbox = []

        self.client.force_login(self.reader)
        url = reverse('kalapila:post_comment')
        response = self.client.post(url, {
            'page_id': self.article_page.id,
            'body': 'Notification check.'
        })
        
        # Check emails sent: should send email to staff_1 only (staff_2 is muted)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.staff_1.email])
        self.assertIn('Notification check.', mail.outbox[0].body)

        # Check DashboardNotification: should create dashboard notifications for staff_1 only (staff_2 is muted)
        self.assertEqual(DashboardNotification.objects.filter(comment__body='Notification check.').count(), 1)
        self.assertTrue(DashboardNotification.objects.filter(user=self.staff_1, comment__body='Notification check.').exists())
        self.assertFalse(DashboardNotification.objects.filter(user=self.staff_2, comment__body='Notification check.').exists())

    def test_comment_reporting(self):
        comment = Comment.objects.create(
            page=self.article_page,
            user=self.staff_2,
            body="Reportable comment"
        )
        
        self.client.force_login(self.reader)
        url = reverse('kalapila:report_comment', kwargs={'pk': comment.pk})
        response = self.client.post(url)
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'success')
        self.assertTrue(CommentReport.objects.filter(comment=comment, user=self.reader).exists())
        self.assertEqual(comment.report_count, 1)

    def test_comment_removal_by_author(self):
        comment = Comment.objects.create(
            page=self.article_page,
            user=self.reader,
            body="My own comment"
        )
        self.client.force_login(self.reader)
        url = reverse('kalapila:delete_comment', kwargs={'pk': comment.pk})
        response = self.client.post(url)
        
        self.assertEqual(response.status_code, 200)
        comment.refresh_from_db()
        self.assertTrue(comment.is_removed)

    def test_comment_removal_by_admin(self):
        comment = Comment.objects.create(
            page=self.article_page,
            user=self.reader,
            body="Reader comment"
        )
        # Login as staff_1 (admin)
        self.client.force_login(self.staff_1)
        url = reverse('kalapila:delete_comment', kwargs={'pk': comment.pk})
        response = self.client.post(url)
        
        self.assertEqual(response.status_code, 200)
        comment.refresh_from_db()
        self.assertTrue(comment.is_removed)

    def test_comment_removal_by_unauthorized_user(self):
        comment = Comment.objects.create(
            page=self.article_page,
            user=self.reader,
            body="Reader comment"
        )
        # Login as restricted_reader (not author and not admin)
        self.client.force_login(self.restricted_reader)
        url = reverse('kalapila:delete_comment', kwargs={'pk': comment.pk})
        response = self.client.post(url)
        
        self.assertEqual(response.status_code, 403)
        comment.refresh_from_db()
        self.assertFalse(comment.is_removed)

    def test_dashboard_notification_polling_and_marking_read(self):
        # Create unread notification for staff_1
        comment = Comment.objects.create(
            page=self.article_page,
            user=self.reader,
            body="Poll me"
        )
        notification = DashboardNotification.objects.create(
            user=self.staff_1,
            comment=comment
        )

        # Login as staff_1
        self.client.force_login(self.staff_1)
        
        # Poll notifications
        poll_url = reverse('kalapila:admin_notifications')
        response = self.client.get(poll_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['notifications']), 1)
        self.assertEqual(response.json()['notifications'][0]['id'], notification.id)

        # Mark read
        read_url = reverse('kalapila:mark_notification_read', kwargs={'pk': notification.id})
        response = self.client.post(read_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'success')
        
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)

    def test_toggle_notifications_preference(self):
        # Login as staff_1 (default preferences: none created -> will default to receiving notifications)
        self.client.force_login(self.staff_1)
        
        toggle_url = reverse('toggle_comment_notifications')
        # GET must not change state (it would let any page flip it via <img>).
        self.assertEqual(self.client.get(toggle_url).status_code, 405)
        self.assertFalse(CommentNotificationPreference.objects.filter(user=self.staff_1).exists())

        response = self.client.post(toggle_url)
        
        # Should redirect back to snippet list view
        self.assertEqual(response.status_code, 302)
        
        pref = CommentNotificationPreference.objects.get(user=self.staff_1)
        self.assertFalse(pref.receive_notifications)


class StaffNotificationDeliveryTests(TestCase):
    """
    Each staff member listens on their own channel group. A shared group
    meant every comment reached every admin once per colleague; and the same
    reader re-flagging a comment notified and emailed all staff each time.
    """

    # Same fixture (pages, readers, two staff with staff_2 muted), without
    # inheriting — and so re-running — every CommentSystemTests test.
    setUp = CommentSystemTests.setUp

    def _post_with_layer(self, url, data=None):
        from unittest.mock import AsyncMock, MagicMock, patch

        layer = MagicMock()
        layer.group_send = AsyncMock()
        with patch("kalapila.views.get_channel_layer", return_value=layer):
            response = self.client.post(url, data or {})
        groups = [c.args[0] for c in layer.group_send.call_args_list]
        return response, groups

    def test_each_staff_member_gets_one_toast_on_their_own_group(self):
        self.client.force_login(self.reader)
        response, groups = self._post_with_layer(
            reverse('kalapila:post_comment'), {'page_id': self.article_page.id, 'body': 'Hello'}
        )
        self.assertEqual(response.status_code, 200)
        admin_groups = [g for g in groups if g.startswith("admin_notifications")]
        # staff_2 muted notifications; staff_1 gets exactly one.
        self.assertEqual(admin_groups, [f"admin_notifications_{self.staff_1.pk}"])

    def test_consumer_joins_its_own_group(self):
        from kalapila.consumers import staff_notification_group

        self.assertEqual(staff_notification_group(self.staff_1.pk), f"admin_notifications_{self.staff_1.pk}")

    def test_repeat_report_by_the_same_reader_notifies_nobody(self):
        comment = Comment.objects.create(page=self.article_page, user=self.staff_2, body="Flag me")
        self.client.force_login(self.reader)
        url = reverse('kalapila:report_comment', kwargs={'pk': comment.pk})

        self._post_with_layer(url)
        notes = DashboardNotification.objects.filter(comment=comment).count()
        emails = len(mail.outbox)
        self.assertEqual(notes, 1)

        response, groups = self._post_with_layer(url)
        self.assertEqual(response.json()['status'], 'success')
        self.assertEqual(groups, [])
        self.assertEqual(DashboardNotification.objects.filter(comment=comment).count(), notes)
        self.assertEqual(len(mail.outbox), emails)

    def test_notification_text_never_contains_the_phone_number(self):
        self.client.force_login(self.reader)
        self._post_with_layer(reverse('kalapila:post_comment'), {'page_id': self.article_page.id, 'body': 'Hi'})
        for note in DashboardNotification.objects.all():
            self.assertNotIn("+1555", note.message)
