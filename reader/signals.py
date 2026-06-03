import logging
from django.dispatch import receiver
from wagtail.signals import page_published

logger = logging.getLogger(__name__)


@receiver(page_published)
def on_issue_published(sender, instance, **kwargs):
    """
    Fires after every Wagtail page publish.
    We only act when the page is an Issue — everything else falls through.

    The newsletter call is wrapped in a broad try/except so that a broken
    email config can never roll back a legitimate publish action.
    """
    # Late import: avoids circular dependency since reader and issue
    # apps both import from each other at module level in places.
    try:
        from issue.models import Issue
    except ImportError:
        logger.warning("[Newsletter] Could not import issue.models.Issue — signal ignored.")
        return

    if not isinstance(instance, Issue):
        return  # Not an Issue page — nothing to do

    logger.info(
        "[Newsletter] Issue published: '%s' (pk=%s). Sending newsletter.",
        instance.title, instance.pk,
    )

    try:
        from reader.newsletter import send_new_issue_newsletter
        send_new_issue_newsletter(instance)
    except Exception:
        # Log the full traceback but never propagate — publish must succeed
        logger.exception(
            "[Newsletter] Unhandled error while sending newsletter for issue pk=%s.",
            instance.pk,
        )
