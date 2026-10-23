"""
One-time "set your password" links for accounts created on someone's
behalf (author pages create their reader account automatically).

Uses allauth's own password-reset token, so the link lands on the normal
reset page, expires after PASSWORD_RESET_TIMEOUT (3 days by default) and
stops working once the password has been set.
"""
from allauth.account.forms import default_token_generator
from allauth.account.utils import user_pk_to_url_str
from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse


def password_setup_url(user):
    path = reverse(
        "account_reset_password_from_key",
        kwargs={
            "uidb36": user_pk_to_url_str(user),
            "key": default_token_generator.make_token(user),
        },
    )
    return f"{settings.BASE_URL}{path}"


def send_password_setup_email(user, *, name=None):
    """Email `user` a link to choose a password. Returns True if sent."""
    if not user.email:
        return False
    site = settings.WAGTAIL_SITE_NAME
    send_mail(
        f"Set the password for your {site} account",
        (
            f"Dear {name or user.name},\n\n"
            f"An author page has been created for you on {site}, together "
            f"with a reader account for your phone number.\n\n"
            f"Choose your password here (the link expires in a few days):\n"
            f"{password_setup_url(user)}\n\n"
            f"If the link has expired, use \"Forgot password\" on the login "
            f"page.\n\nThe {site} team\n"
        ),
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        fail_silently=False,
    )
    return True
