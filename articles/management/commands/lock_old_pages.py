"""
Management command: lock_old_pages

Manually triggers the page-locking task — either synchronously in the
current process, or asynchronously via Django-Q2.

Usage
-----
    # Run synchronously with the default threshold from settings
    python manage.py lock_old_pages

    # Override threshold without touching settings
    python manage.py lock_old_pages --days 30

    # Preview which pages would be locked without making any changes
    python manage.py lock_old_pages --dry-run

    # Queue as a background Q2 task (qcluster must be running)
    python manage.py lock_old_pages --async
    python manage.py lock_old_pages --days 14 --async
"""
from django.core.management.base import BaseCommand
from django.conf import settings


class Command(BaseCommand):
    help = (
        "Lock Article and Issue pages that were first published more than N days ago. "
        "Protects archive content from accidental edits. "
        "Use --dry-run to preview, --async to hand off to qcluster."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help=(
                "Lock pages published more than this many days ago. "
                "Defaults to PAGE_LOCK_DAYS in settings "
                f"(currently {getattr(settings, 'PAGE_LOCK_DAYS', 10)})."
            ),
        )
        parser.add_argument(
            "--async",
            action="store_true",
            dest="use_async",
            help=(
                "Queue the task via Django-Q2 instead of running synchronously. "
                "Requires qcluster to be running."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            dest="dry_run",
            help="Print which pages would be locked without making any changes.",
        )

    def handle(self, *args, **options):
        days = options["days"] or getattr(settings, "PAGE_LOCK_DAYS", 10)

        if options["dry_run"]:
            self._dry_run(days)
            return

        self.stdout.write(
            f"Locking pages first published more than {days} day(s) ago..."
        )

        if options["use_async"]:
            self._queue_async(days)
        else:
            self._run_sync(days)

    # ── Execution helpers ────────────────────────────────────────────────

    def _run_sync(self, days):
        from articles.tasks import lock_old_pages_task

        result = lock_old_pages_task(days=days)
        self.stdout.write(
            self.style.SUCCESS(
                f"Done — locked {result['articles_locked']} article(s) and "
                f"{result['issues_locked']} issue(s) "
                f"(cutoff: {result['cutoff_date']}, threshold: {result['threshold_days']} day(s))."
            )
        )

    def _queue_async(self, days):
        try:
            from django_q.tasks import async_task
        except ImportError:
            self.stderr.write(
                self.style.ERROR(
                    "django_q is not installed. Omit --async to run synchronously."
                )
            )
            return

        task_id = async_task(
            "articles.tasks.lock_old_pages_task",
            days=days,
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Queued as Django-Q2 task — ID: {task_id}\n"
                "Results will appear in the Q2 admin once qcluster picks up the task."
            )
        )

    def _dry_run(self, days):
        from datetime import timedelta
        from django.utils import timezone
        from articles.models import Article
        from issue.models import Issue

        cutoff = timezone.now() - timedelta(days=days)

        articles = (
            Article.objects.live()
            .filter(
                locked=False,
                first_published_at__isnull=False,
                first_published_at__lte=cutoff,
            )
            .order_by("first_published_at")
        )

        issues = (
            Issue.objects.live()
            .filter(
                locked=False,
                first_published_at__isnull=False,
                first_published_at__lte=cutoff,
            )
            .order_by("first_published_at")
        )

        self.stdout.write(
            self.style.WARNING(
                f"DRY RUN — pages that would be locked "
                f"(cutoff: {cutoff.date()}, threshold: {days} day(s)):"
            )
        )

        self.stdout.write(f"\n  Articles ({articles.count()}):")
        for article in articles:
            self.stdout.write(
                f"    [pk={article.pk}] {article.title} "
                f"— first published {article.first_published_at.date()}"
            )

        self.stdout.write(f"\n  Issues ({issues.count()}):")
        for issue in issues:
            self.stdout.write(
                f"    [pk={issue.pk}] {issue.title} "
                f"— first published {issue.first_published_at.date()}"
            )

        self.stdout.write(
            f"\n  Total: {articles.count()} article(s) + {issues.count()} issue(s) "
            "would be locked. No changes made."
        )
