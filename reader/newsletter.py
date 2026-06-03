import logging
from django.core.mail import EmailMultiAlternatives
from django.conf import settings

logger = logging.getLogger(__name__)


def send_new_issue_newsletter(issue):
    """
    Send a new-issue notification to every opted-in, active reader
    who has an email address. Readers without an email are counted
    and logged but never raise an error — email is not a required field.

    In development, add this to settings.py and emails are printed to the terminal:
        EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

    Returns (sent_count, failed_count).
    """
    from reader.models import ReaderUser

    # --- Recipient set ---------------------------------------------------
    # All opted-in, active, non-anonymised readers
    opted_in = ReaderUser.objects.filter(
        newsletter_opt_in=True,
        is_active=True,
        is_anonymized=False,
    )

    # Only those with an actual email address — never required, never an error
    with_email = opted_in.filter(email__isnull=False).exclude(email='')
    skipped_no_email = opted_in.count() - with_email.count()

    if skipped_no_email:
        logger.info(
            "[Newsletter] %d opted-in reader(s) have no email on file — skipped silently.",
            skipped_no_email,
        )

    if not with_email.exists():
        logger.info("[Newsletter] No email recipients found. Nothing sent.")
        return 0, 0

    # --- Email content ---------------------------------------------------
    issue_url   = getattr(issue, 'full_url', '') or ''
    issue_title = getattr(issue, 'title', 'New Issue')

    # Base URL for unsubscribe link — fall back gracefully in dev
    base_url    = getattr(settings, 'BASE_URL', '').rstrip('/')
    profile_url = f"{base_url}/reader/profile/"

    sent_count   = 0
    failed_count = 0

    for reader in with_email:
        try:
            subject = f"ഇക്കാരസ് — {issue_title}"

            # Plain-text body — always included as fallback for email clients
            # that don't render HTML
            text_body = (
                f"Hello {reader.name},\n\n"
                f"A new issue of Icarus has just been published:\n"
                f"  {issue_title}\n\n"
                f"Read it here: {issue_url}\n\n"
                f"— The Icarus Team\n\n"
                f"─────────────────────────────────────────\n"
                f"You are receiving this because you opted in to the Icarus newsletter.\n"
                f"To unsubscribe, visit your profile page:\n"
                f"  {profile_url}\n"
            )

            # HTML body — intentionally minimal; swap this for a proper
            # template render (render_to_string) when going to production
            html_body = f"""
<div style="font-family:sans-serif;max-width:560px;margin:0 auto;
            padding:24px;color:#1a1a1a;background:#fff;">

  <h2 style="font-size:13px;letter-spacing:.12em;text-transform:uppercase;
             color:#c0392b;margin:0 0 4px;">ഇക്കാരസ്</h2>

  <p style="margin:0 0 20px;">Hello {reader.name},</p>

  <p style="margin:0 0 8px;">A new issue has just been published:</p>
  <h3 style="margin:0 0 24px;font-size:20px;">{issue_title}</h3>

  <a href="{issue_url}"
     style="display:inline-block;padding:10px 22px;
            background:#c0392b;color:#fff;text-decoration:none;
            border-radius:4px;font-size:14px;">
    Read now &rarr;
  </a>

  <hr style="border:none;border-top:1px solid #eee;margin:32px 0 16px;">
  <p style="font-size:11px;color:#999;margin:0;">
    You&rsquo;re receiving this because you opted in to the Icarus newsletter.<br>
    <a href="{profile_url}" style="color:#999;">Unsubscribe in your profile</a>
  </p>

</div>
"""

            email = EmailMultiAlternatives(
                subject=subject,
                body=text_body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[reader.email],
            )
            email.attach_alternative(html_body, "text/html")
            email.send()
            sent_count += 1

        except Exception as e:
            # Log and continue — one bad address must never stop the rest
            logger.error(
                "[Newsletter] Failed to send to reader pk=%s: %s",
                reader.pk, e,
            )
            failed_count += 1

    logger.info(
        "[Newsletter] Complete. Sent: %d | Failed: %d | Skipped (no email): %d",
        sent_count, failed_count, skipped_no_email,
    )
    return sent_count, failed_count
