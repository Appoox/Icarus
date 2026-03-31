"""
Management command: ingest_archive

CLI fallback for ingesting PDFs from the archive directory.
Usage:
    python manage.py ingest_archive
    python manage.py ingest_archive --force   # re-ingest already processed PDFs
"""
from django.core.management.base import BaseCommand
from the_librarian.services.ingestion import ingest_archive


class Command(BaseCommand):
    help = "Ingest PDFs from the archive directory: OCR → chunk → embed → pgvector"

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-ingest PDFs that have already been processed",
        )

    def handle(self, *args, **options):
        force = options["force"]

        if force:
            self.stdout.write(self.style.WARNING("Force mode: re-ingesting all PDFs"))

        self.stdout.write("Starting archive ingestion...\n")
        results = ingest_archive(force=force)

        # Print results
        for result in results["processed"]:
            self.stdout.write(
                self.style.SUCCESS(f"  ✓ {result['filename']}: {result['message']}")
            )

        for result in results["skipped"]:
            self.stdout.write(f"  ⊘ {result['filename']}: {result['message']}")

        for result in results["errors"]:
            self.stdout.write(
                self.style.ERROR(f"  ✗ {result['filename']}: {result['message']}")
            )

        self.stdout.write(
            f"\nDone — processed: {len(results['processed'])}, "
            f"skipped: {len(results['skipped'])}, "
            f"errors: {len(results['errors'])}"
        )
