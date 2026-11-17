"""
Management command: backup

Runs an off-server backup, verification or setup check in this process,
printing its log as it goes. The dashboard and the nightly schedule run the
same code on the qcluster worker; this is for setting up and debugging.

Run it in the qcluster container, which has the rclone config and the
local backup folder mounted (the web container has neither):

    docker compose exec qcluster python manage.py backup --check
    docker compose exec qcluster python manage.py backup
    docker compose exec qcluster python manage.py backup --verify [--snapshot 20261116T210000Z]
"""
from django.core.management.base import BaseCommand, CommandError

from backups import engine
from backups.models import BackupRun


class Command(BaseCommand):
    help = "Run an off-server backup (or --verify / --check) now, in this process."

    def add_arguments(self, parser):
        what = parser.add_mutually_exclusive_group()
        what.add_argument(
            "--verify", action="store_true",
            help="Download a snapshot from the remote and prove it restores, instead of backing up.",
        )
        what.add_argument(
            "--check", action="store_true",
            help="Only check the setup: pg_dump, rclone, the remote, encryption, storage.",
        )
        parser.add_argument(
            "--snapshot", help="With --verify: the snapshot to check (default: the newest)."
        )

    def handle(self, *args, verify=False, check=False, snapshot=None, **options):
        if snapshot and not verify:
            raise CommandError("--snapshot only goes with --verify.")
        kind = (
            BackupRun.Kind.VERIFY if verify
            else BackupRun.Kind.CHECK if check
            else BackupRun.Kind.BACKUP
        )
        run = BackupRun.objects.create(kind=kind, trigger=BackupRun.Trigger.COMMAND)
        try:
            engine.perform(run, echo=self.stdout.write, snapshot=snapshot)
        except Exception as exc:
            raise CommandError(f"{run.get_kind_display()} #{run.pk} failed: {run.error or exc}") from exc
        self.stdout.write(self.style.SUCCESS(f"{run.get_kind_display()} #{run.pk} succeeded."))
