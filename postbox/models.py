from django.db import models
from django.conf import settings
from wagtail.admin.panels import FieldPanel, MultiFieldPanel


class Postbox(models.Model):
    FEEDBACK_TYPES = [
        ('bug',        'Bug Report'),
        ('suggestion', 'Suggestion'),
        ('content',    'Content Issue'),
        ('general',    'General Feedback'),
    ]

    feedback_type = models.CharField(
        max_length=20, choices=FEEDBACK_TYPES, default='general',
        verbose_name='Type',
    )
    feedback     = models.TextField(verbose_name='Feedback')
    rating       = models.PositiveSmallIntegerField(
        null=True, blank=True,
        choices=[(i, i) for i in range(1, 6)],
        verbose_name='Rating',
    )
    page_context = models.CharField(
        max_length=500, blank=True,
        verbose_name='Page context',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='feedback_submissions',
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    is_reviewed  = models.BooleanField(default=False, verbose_name='Reviewed')
    admin_notes  = models.TextField(blank=True, verbose_name='Admin notes')

    # ── Wagtail edit panels ────────────────────────────────────────────
    panels = [
        MultiFieldPanel([
            FieldPanel('feedback_type', read_only=True),
            FieldPanel('rating',        read_only=True),
        ], heading='Type & Rating'),
        FieldPanel('feedback',      read_only=True),
        FieldPanel('page_context', read_only=True),
        FieldPanel('user',         read_only=True),
        FieldPanel('submitted_at', read_only=True),
        MultiFieldPanel([
            FieldPanel('is_reviewed'),
            FieldPanel('admin_notes'),
        ], heading='Review'),
    ]

    class Meta:
        ordering = ['-submitted_at']
        verbose_name = 'Postbox'
        verbose_name_plural = 'Postbox Submissions'

    def __str__(self):
        return (
            f"{self.get_feedback_type_display()} "
            f"by {self.user} — {self.submitted_at:%Y-%m-%d %H:%M}"
        )

    # ── list_display helpers ───────────────────────────────────────────
    def truncated_feedback(self):
        return (self.feedback[:90] + '…') if len(self.feedback) > 90 else self.feedback
    truncated_feedback.short_description = 'Feedback'

    def stars(self):
        return ('★' * self.rating) if self.rating else '—'
    stars.short_description = 'Rating'


class PostboxNotification(models.Model):
    """Tracks unread admin toast alerts, one row per staff member per submission."""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='postbox_notifications',
    )
    postbox = models.ForeignKey(
        Postbox,
        on_delete=models.CASCADE,
        related_name='notifications',
    )
    feedback    = models.CharField(max_length=255, default='')
    is_read    = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Postbox Notification'
        verbose_name_plural = 'Postbox Notifications'

    def __str__(self):
        return f"Notification for {self.user} — Read: {self.is_read}"


class PostboxNotificationPreference(models.Model):
    """Per-staff opt-out from postbox toast notifications."""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='postbox_notification_preference',
    )
    receive_notifications = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.user} — Notifications: {self.receive_notifications}"