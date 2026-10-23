import logging

from django.db import transaction
from django.dispatch import receiver
from wagtail.signals import page_published

from issue.models import Issue

logger = logging.getLogger(__name__)


@receiver(page_published, sender=Issue)
def on_issue_published(sender, instance, **kwargs):
    """
    Queue the new-issue newsletter when an Issue is published.

    Nothing is sent inside the publish request: the task is queued only
    after the publish transaction commits, and reader.tasks makes sure each
    issue is mailed once, however often it is re-published. A queueing
    failure is logged and never blocks the publish.

    Connected from ReaderConfig.ready().
    """
    if instance.newsletter_sent_at is not None:
        return  # already mailed — the common case for re-publishes

    def _queue():
        try:
            from django_q.tasks import async_task
            async_task("reader.tasks.send_issue_newsletter_task", instance.pk)
        except Exception:
            logger.exception("[Newsletter] Could not queue the newsletter for issue pk=%s.", instance.pk)

    transaction.on_commit(_queue)
