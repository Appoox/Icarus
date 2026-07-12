"""
One-off backfill: ensure every existing Literati page has a linked
ReaderUser. The link-or-create logic lives in Literati.save() — this
command just re-saves the pages that are missing one, so there is a
single code path for the invariant.
"""
from django.core.management.base import BaseCommand
from literati.models import Literati


class Command(BaseCommand):
    help = "Link or create a ReaderUser for every Literati page missing one."

    def handle(self, *args, **options):
        missing = Literati.objects.filter(reader_user__isnull=True)
        total = missing.count()
        linked = 0

        for lit in missing.iterator():
            try:
                lit.save()
            except Exception as exc:
                self.stdout.write(self.style.ERROR(
                    f"  ✗ {lit.title} (pk={lit.pk}): {exc}"
                ))
                continue

            lit.refresh_from_db(fields=['reader_user'])
            if lit.reader_user_id:
                linked += 1
            else:
                self.stdout.write(self.style.WARNING(
                    f"  ! {lit.title} (pk={lit.pk}) still unlinked — no phone number?"
                ))

        self.stdout.write(self.style.SUCCESS(
            f"{linked}/{total} previously unlinked Literati pages now have reader accounts."
        ))
