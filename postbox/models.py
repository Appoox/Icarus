from django.db import models
from django.conf import settings
from django.utils.html import format_html # Added format_html for image preview rendering
from wagtail.admin.panels import FieldPanel, MultiFieldPanel, Panel
from django.utils.translation import gettext_lazy as _

# ── Custom Wagtail Panel for Image Preview ─────────────────────────
class ImagePreviewPanel(Panel):
    """Custom Wagtail panel to visually display an uploaded image inside the admin snippet view."""
    class BoundPanel(Panel.BoundPanel):
        def render_html(self, parent_context=None):
            # Safely check if the instance exists and actually has a file attached
            if getattr(self.instance, 'image', None) and self.instance.image:
                return format_html(
                    '<div style="margin-top: 1rem; padding-top: 1rem; border-top: 1px solid #e2e8f0;">'
                    '<p style="display: block; font-weight: 600; margin-bottom: 0.5rem; font-size: 0.85rem; color: #333;">Visual Preview:</p>'
                    '<a href="{}" target="_blank" style="display: inline-block;">'
                    '<img src="{}" style="max-height: 400px; max-width: 100%; border: 1px solid #ccc; border-radius: 4px;" alt="User Feedback Attachment" />'
                    '</a>'
                    '</div>',
                    self.instance.image.url,
                    self.instance.image.url
                )
            return '<div style="margin-top: 1rem; padding-top: 1rem; border-top: 1px solid #e2e8f0; color: #64748b; font-size: 0.85rem;">No visual preview available.</div>'


class Postbox(models.Model):
    FEEDBACK_TYPES = [
        ('bug',        _('ബഗ് റിപ്പോർട്ട്')),
        ('suggestion', _('നിർദ്ദേശം')),
        ('content',    _('ഉള്ളടക്കത്തിലെ പ്രശ്നം')),
        ('general',    _('പൊതുവായ ഫീഡ്‌ബാക്ക്')),
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
    # Added ImageField to allow users to attach a screenshot or image to their feedback
    image = models.ImageField(
        upload_to='postbox_images/', 
        null=True, 
        blank=True, 
        verbose_name='Attached Image'
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
        # Added a panel group to show the image link and injected our custom ImagePreviewPanel for visual rendering
        MultiFieldPanel([
            FieldPanel('image', read_only=True),
            ImagePreviewPanel(),
        ], heading='User Attachment'),
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

    # Custom property method to visually render the uploaded image inside standard Django admin dashboard
    def image_preview(self):
        if self.image:
            return format_html(
                '<img src="{}" style="max-height: 300px; max-width: 100%; border-radius: 4px; border: 1px solid #ccc;" />',
                self.image.url
            )
        return "No image provided"
    image_preview.short_description = 'Image Preview'


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