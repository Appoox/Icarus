from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        "Send (or preview) the newsletter for a published issue. "
        "Defaults to the most recently published issue. "
        "Only readers who have opted in AND have an email address on file receive it."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--issue-id',
            type=int,
            default=None,
            metavar='PK',
            help='Issue page ID to send for. Omit to use the most recent published issue.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Print the recipient list without actually sending any email.',
        )

    def handle(self, *args, **options):
        from issue.models import Issue
        from reader.models import ReaderUser
        from reader.newsletter import send_new_issue_newsletter

        # ── Resolve issue ─────────────────────────────────────────────────
        issue_id = options['issue_id']
        if issue_id:
            try:
                issue = Issue.objects.get(pk=issue_id)
            except Issue.DoesNotExist:
                raise CommandError(f"No Issue found with pk={issue_id}.")
        else:
            issue = Issue.objects.live().order_by('-first_published_at').first()
            if not issue:
                raise CommandError("No published issues found in the database.")

        self.stdout.write(f"\nIssue : {issue.title} (pk={issue.pk})")
        self.stdout.write(f"URL   : {getattr(issue, 'full_url', 'n/a')}\n")

        # ── Recipient summary ─────────────────────────────────────────────
        opted_in = ReaderUser.objects.filter(
            newsletter_opt_in=True,
            is_active=True,
            is_anonymized=False,
        )
        with_email    = opted_in.filter(email__isnull=False).exclude(email='')
        without_email = opted_in.count() - with_email.count()

        self.stdout.write(f"Recipients (with email)    : {with_email.count()}")
        if without_email:
            self.stdout.write(
                self.style.WARNING(
                    f"Skipped   (no email on file): {without_email} "
                    f"— email is not required; these readers are simply skipped."
                )
            )

        # ── Dry run ───────────────────────────────────────────────────────
        if options['dry_run']:
            if with_email.exists():
                self.stdout.write("\nWould send to:")
                for r in with_email.order_by('name'):
                    self.stdout.write(f"  {r.name:<40} <{r.email}>")
            self.stdout.write(self.style.WARNING("\nDry run — nothing sent."))
            return

        # ── Live send ─────────────────────────────────────────────────────
        self.stdout.write("\nSending…")
        sent, failed = send_new_issue_newsletter(issue)

        if failed:
            self.stdout.write(
                self.style.ERROR(f"\nDone. Sent: {sent} | Failed: {failed}")
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f"\nDone. Sent: {sent} | Failed: {failed}")
            )
