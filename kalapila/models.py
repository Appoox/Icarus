from django.db import models
from django.conf import settings
from wagtail.contrib.settings.models import BaseSiteSetting, register_setting
from wagtail.admin.panels import FieldPanel

class Comment(models.Model):
    page = models.ForeignKey('wagtailcore.Page', on_delete=models.CASCADE,
                             related_name='comments')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='comments')
    parent = models.ForeignKey('self', null=True, blank=True,
                               on_delete=models.CASCADE, related_name='replies')
    body = models.TextField(max_length=3000)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_approved = models.BooleanField(default=True)
    is_removed = models.BooleanField(default=False)
    removal_reason = models.CharField(max_length=255, blank=True)

    panels = [
        FieldPanel('page', read_only=True),
        FieldPanel('user', read_only=True),
        FieldPanel('parent', read_only=True),
        FieldPanel('body', read_only=True),
        FieldPanel('is_approved'),
        FieldPanel('is_removed'),
        FieldPanel('removal_reason'),
    ]

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['page', 'created_at']),
        ]

    def __str__(self):
        return f"{self.user_display} on {self.page_display} ({self.created_at.strftime('%Y-%m-%d')})"

    @property
    def user_display(self):
        return self.user.name or str(self.user.phone_number)

    @property
    def page_display(self):
        return self.page.title

    @property
    def body_truncated(self):
        if len(self.body) > 50:
            return self.body[:50] + "..."
        return self.body

    @property
    def report_count(self):
        return self.reports.count()

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        old_is_approved = False
        old_is_removed = False

        if not is_new:
            try:
                old_instance = Comment.objects.get(pk=self.pk)
                old_is_approved = old_instance.is_approved
                old_is_removed = old_instance.is_removed
            except Comment.DoesNotExist:
                pass

        super().save(*args, **kwargs)

        if not is_new:
            # 1. Approved notification: transition from False -> True
            if not old_is_approved and self.is_approved:
                UserNotification.objects.get_or_create(
                    user=self.user,
                    message=f"നിങ്ങളുടെ അഭിപ്രായം '{self.page_display}' എന്ന പേജിൽ പ്രസിദ്ധീകരിച്ചു."
                )
            # 2. Removed notification: transition from False -> True
            if not old_is_removed and self.is_removed:
                if not getattr(self, '_deleted_by_author', False):
                    UserNotification.objects.get_or_create(
                        user=self.user,
                        message=f"നിങ്ങളുടെ അഭിപ്രായം '{self.page_display}'  എന്ന പേജിൽ നിന്ന് '{self.removal_reason}' എന്ന് കാരണത്താൽ ഒഴിവാക്കി."
                    )


class CommentReport(models.Model):
    """Stores user flags/reports for inappropriate comments."""
    comment = models.ForeignKey(Comment, on_delete=models.CASCADE, related_name='reports')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('comment', 'user')

    def __str__(self):
        user_display = self.user.name or str(self.user.phone_number)
        return f"Report by {user_display} on comment {self.comment_id}"

class CommentNotificationPreference(models.Model):
    """User preferences for staff notification emails."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name='comment_notification_preference')
    receive_notifications = models.BooleanField(default=True)

    def __str__(self):
        user_display = self.user.name or str(self.user.phone_number)
        return f"{user_display} - Notifications: {self.receive_notifications}"

@register_setting
class CommentSettings(BaseSiteSetting):
    """Dashboard killswitch for comment moderation."""
    global_premoderation = models.BooleanField(
        default=False,
        help_text="When ON, ALL new comments site-wide require admin approval before appearing."
    )
    premoderated_pages = models.ManyToManyField(
        'wagtailcore.Page', blank=True,
        help_text="Specific pages (Articles or Issues) where new comments require admin approval."
    )

    panels = [
        FieldPanel('global_premoderation'),
        FieldPanel('premoderated_pages'),
    ]

    class Meta:
        verbose_name = "Comment Moderation Settings"

class DashboardNotification(models.Model):
    """Tracks unread notification alerts for the Wagtail admin dashboard pop-up."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='dashboard_notifications')
    comment = models.ForeignKey(Comment, on_delete=models.CASCADE)
    message = models.CharField(max_length=255, default='')
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        user_display = self.user.name or str(self.user.phone_number)
        return f"Notification for {user_display} - Read: {self.is_read}"

class UserNotification(models.Model):
    """Tracks status update notifications for comment authors."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='user_notifications')
    message = models.CharField(max_length=255)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        user_display = self.user.name or str(self.user.phone_number)
        return f"User Notification for {user_display} - Read: {self.is_read}"
