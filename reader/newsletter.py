"""
New-issue newsletter: recipients, rendering, sending, one-click unsubscribe.

Sending is triggered by reader.signals (on publish, queued through
django-q) and by `manage.py send_newsletter`. The once-per-issue guarantee
lives in reader.tasks; this module just builds and sends the mail.
"""
import logging

from django.conf import settings
from django.core import signing
from django.core.mail import EmailMultiAlternatives, get_connection
from django.template.loader import render_to_string
from django.urls import reverse

logger = logging.getLogger(__name__)

_UNSUBSCRIBE_SALT = "reader.newsletter.unsubscribe"


def newsletter_recipients():
    """Opted-in, active, non-anonymised readers who have an email address."""
    from reader.models import ReaderUser

    return (
        ReaderUser.objects.filter(newsletter_opt_in=True, is_active=True, is_anonymized=False)
        .filter(email__isnull=False)
        .exclude(email="")
    )


def unsubscribe_token(reader):
    """A signed token naming the reader. No expiry: old emails keep working."""
    return signing.dumps({"r": reader.pk}, salt=_UNSUBSCRIBE_SALT)


def reader_pk_from_token(token):
    """The reader pk inside a token, or None if it was tampered with."""
    try:
        return signing.loads(token, salt=_UNSUBSCRIBE_SALT)["r"]
    except (signing.BadSignature, KeyError, TypeError):
        return None


def unsubscribe_url(reader):
    return f"{settings.BASE_URL}{reverse('newsletter_unsubscribe', args=[unsubscribe_token(reader)])}"


def _message(issue, reader, connection):
    context = {
        "site_name": settings.WAGTAIL_SITE_NAME,
        "issue": issue,
        "issue_url": getattr(issue, "full_url", "") or settings.BASE_URL,
        "reader": reader,
        "unsubscribe_url": unsubscribe_url(reader),
    }
    subject = render_to_string("reader/email/new_issue_subject.txt", context).strip()
    msg = EmailMultiAlternatives(
        subject=subject,
        body=render_to_string("reader/email/new_issue.txt", context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[reader.email],
        connection=connection,
        headers={
            # RFC 8058 one-click unsubscribe (Gmail / Yahoo bulk-sender rules).
            "List-Unsubscribe": f"<{context['unsubscribe_url']}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
    )
    # Templates autoescape, so a reader's name or an issue title can't
    # inject markup into the HTML part.
    msg.attach_alternative(render_to_string("reader/email/new_issue.html", context), "text/html")
    return msg


def send_new_issue_newsletter(issue):
    """
    Email `issue` to every newsletter recipient over a single SMTP connection.
    A failure for one address is logged and counted, never raised.

    Returns (sent_count, failed_count).
    """
    recipients = newsletter_recipients()
    if not recipients.exists():
        logger.info("[Newsletter] No recipients for issue pk=%s.", issue.pk)
        return 0, 0

    sent = failed = 0
    with get_connection() as connection:
        for reader in recipients.iterator():
            try:
                _message(issue, reader, connection).send()
                sent += 1
            except Exception:
                failed += 1
                logger.exception("[Newsletter] Failed to send issue pk=%s to reader pk=%s.", issue.pk, reader.pk)

    logger.info("[Newsletter] Issue pk=%s: sent %d, failed %d.", issue.pk, sent, failed)
    return sent, failed
