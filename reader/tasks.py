"""Background tasks for the reader app (run by the qcluster service)."""
import logging

from django.utils import timezone

logger = logging.getLogger(__name__)


def send_issue_newsletter_task(issue_pk):
    """
    Send the new-issue newsletter for `issue_pk` exactly once.

    The issue row is claimed atomically (newsletter_sent_at NULL → now)
    before anything is sent, so a re-publish, a duplicate queue entry or a
    second worker finds it already claimed and does nothing. If sending
    can't start at all (e.g. the mail server is down), the claim is released
    and the error re-raised, so django-q's retry can try again — nobody has
    been mailed at that point.
    """
    from issue.models import Issue
    from reader.newsletter import send_new_issue_newsletter

    claimed = Issue.objects.filter(pk=issue_pk, newsletter_sent_at__isnull=True).update(
        newsletter_sent_at=timezone.now()
    )
    if not claimed:
        logger.info("[Newsletter] Issue pk=%s already sent (or gone); skipping.", issue_pk)
        return "skipped"

    issue = Issue.objects.get(pk=issue_pk)
    try:
        sent, failed = send_new_issue_newsletter(issue)
    except Exception:
        Issue.objects.filter(pk=issue_pk).update(newsletter_sent_at=None)
        raise
    return f"sent={sent} failed={failed}"
