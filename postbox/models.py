from django.db import models
from django.conf import settings


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
    feedback = models.TextField(verbose_name='Feedback')
    rating = models.PositiveSmallIntegerField(
        null=True, blank=True,
        choices=[(i, i) for i in range(1, 6)],
        verbose_name='Rating',
    )
    # Stores the page the user was on when they decided to leave feedback
    page_context = models.CharField(
        max_length=500, blank=True,
        verbose_name='Page context',
    )
    # Non-nullable: only authenticated users reach this form
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='feedback_submissions',
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    is_reviewed  = models.BooleanField(default=False, verbose_name='Reviewed')
    admin_notes  = models.TextField(blank=True, verbose_name='Admin notes')

    class Meta:
        ordering = ['-submitted_at']
        verbose_name = 'Postbox'
        verbose_name_plural = 'Postbox Submissions'

    def __str__(self):
        return (
            f"{self.get_feedback_type_display()} "
            f"by {self.user} — {self.submitted_at:%Y-%m-%d %H:%M}"
        )