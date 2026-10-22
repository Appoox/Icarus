"""
Disable the guessable passwords that author accounts used to be given.

For a while, creating an author page created a reader account with the
password "<phone number>123", and sent it by email, log line and admin
message. This command finds every author account whose password is still
exactly that, makes it unusable, and (with --email) sends the author a
one-time link to choose a new one.

    python manage.py reset_author_passwords --dry-run
    python manage.py reset_author_passwords --email

Accounts whose owners already chose their own password are left alone.
Nothing identifying is printed — only counts and account ids.
"""
from django.core.management.base import BaseCommand

from literati.models import Literati
from reader.password_setup import send_password_setup_email


def _guessable_password(phone):
    return f"{str(phone).replace(' ', '').replace('-', '')}123"


class Command(BaseCommand):
    help = "Make '<phone>123' author passwords unusable and optionally email set-password links."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Only report how many accounts are affected.")
        parser.add_argument("--email", action="store_true",
                            help="Email each affected author a link to set a new password.")

    def handle(self, *args, dry_run=False, email=False, **options):
        authors = (
            Literati.objects.filter(reader_user__isnull=False)
            .select_related("reader_user")
        )
        affected = emailed = no_email = 0
        seen = set()
        for page in authors.iterator():
            user = page.reader_user
            if user.pk in seen or not user.has_usable_password():
                continue
            seen.add(user.pk)
            phone = user.phone_number_encrypted or page.phone_number
            if not phone or not user.check_password(_guessable_password(phone)):
                continue

            affected += 1
            if dry_run:
                continue
            user.set_unusable_password()
            user.save(update_fields=["password"])
            if email:
                if user.email:
                    try:
                        send_password_setup_email(user, name=page.title)
                        emailed += 1
                    except Exception as exc:  # keep going; report at the end
                        self.stderr.write(f"Account {user.pk}: email failed ({exc.__class__.__name__})")
                else:
                    no_email += 1

        verb = "would be reset" if dry_run else "reset"
        self.stdout.write(f"{affected} author account(s) with a guessable password {verb}.")
        if email and not dry_run:
            self.stdout.write(f"{emailed} set-password email(s) sent; {no_email} account(s) have no email address.")
