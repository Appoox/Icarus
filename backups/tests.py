"""
Tests for the off-server backups: the engine (against real pg_dump, rclone
and an encrypted local remote standing in for Google Drive), the retention
policy, the health alert, the schedules and the admin dashboard.
"""
import itertools
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest import mock, skipUnless

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core import mail
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from backups import engine
from backups.health import backup_health
from backups.models import BackupRun


def _tools_available():
    """pg_dump new enough for this server, pg_restore and rclone on PATH."""
    if not (shutil.which("pg_dump") and shutil.which("pg_restore") and shutil.which("rclone")):
        return False
    out = subprocess.run(["pg_dump", "--version"], capture_output=True, text=True).stdout
    match = re.search(r"\)\s*(\d+)", out)
    connection.ensure_connection()
    return bool(match) and int(match.group(1)) >= connection.pg_version // 10000


def _obscure(password):
    return subprocess.run(
        ["rclone", "obscure", password], capture_output=True, text=True, check=True
    ).stdout.strip()


class RetentionPlanTests(TestCase):
    def test_keeps_the_newest_daily_snapshots(self):
        ids = [f"202611{day:02d}T210000Z" for day in range(1, 11)]
        keep, monthly = engine.plan_retention(ids, keep_daily=3, keep_monthly=0)
        self.assertEqual(keep, set(ids[-3:]))
        self.assertEqual(monthly, set())

    def test_keeps_the_first_snapshot_of_each_recent_month(self):
        ids = [
            "20260801T210000Z", "20260815T210000Z",
            "20260902T210000Z", "20260920T210000Z",
            "20261001T210000Z", "20261030T210000Z",
            "20261101T210000Z", "20261102T210000Z",
        ]
        keep, monthly = engine.plan_retention(ids, keep_daily=2, keep_monthly=3)
        self.assertEqual(monthly, {"20260902T210000Z", "20261001T210000Z", "20261101T210000Z"})
        self.assertEqual(keep, monthly | {"20261102T210000Z"})

    def test_counts_snapshots_not_days(self):
        # A month without backups must not age out the last good ones.
        ids = ["20250101T210000Z", "20250102T210000Z"]
        keep, _ = engine.plan_retention(ids, keep_daily=30, keep_monthly=0)
        self.assertEqual(keep, set(ids))


class SnapshotIdTests(TestCase):
    def test_snapshot_ids_are_utc_timestamps_that_sort_in_time_order(self):
        from datetime import datetime, timezone as dt_timezone
        from zoneinfo import ZoneInfo

        ist = datetime(2026, 11, 17, 2, 30, 5, tzinfo=ZoneInfo("Asia/Kolkata"))
        self.assertEqual(engine.new_snapshot_id(ist), "20261116T210005Z")
        self.assertRegex(engine.new_snapshot_id(), engine.SNAPSHOT_RE)
        self.assertEqual(
            engine.snapshot_time("20261116T210005Z"), datetime(2026, 11, 16, 21, 0, 5, tzinfo=dt_timezone.utc)
        )
        self.assertIsNone(engine.snapshot_time("not-a-snapshot"))


class RemotePathTests(TestCase):
    def test_joins_onto_a_bare_remote_or_a_folder(self):
        with self.settings(BACKUP_REMOTE="icarus-backup:"):
            self.assertEqual(engine.remote_path("db", "x.dump"), "icarus-backup:db/x.dump")
            self.assertEqual(engine.remote_name(), "icarus-backup")
        with self.settings(BACKUP_REMOTE="gdrive:icarus-backups"):
            self.assertEqual(engine.remote_path("media/current"), "gdrive:icarus-backups/media/current")
        with self.settings(BACKUP_REMOTE="/srv/backups"):
            self.assertEqual(engine.remote_path("db"), "/srv/backups/db")
            self.assertIsNone(engine.remote_name())

    def test_parses_listremotes(self):
        self.assertEqual(
            engine.parse_listremotes("gdrive:        drive\nicarus-backup: crypt\n"),
            {"gdrive": "drive", "icarus-backup": "crypt"},
        )


@skipUnless(_tools_available(), "needs pg_dump (>= the server's version), pg_restore and rclone")
class EngineEndToEndTests(TestCase):
    """
    Real pg_dump and rclone against a crypt remote over a local folder: the
    same layering as production (crypt over Google Drive), minus the network.
    """

    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="icarus-backup-e2e-"))
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)
        self.store = self.scratch / "drive"      # what "Google Drive" holds
        self.media = self.scratch / "media"
        self.local = self.scratch / "local"
        self.media.mkdir()
        (self.media / "images").mkdir()
        (self.media / "images" / "cover.png").write_bytes(b"png" * 100)
        (self.media / "notes.txt").write_text("first")
        self.env_file = self.scratch / "server.env"
        self.env_file.write_text("SECRET_KEY=not-a-real-one\n")
        self.config = self.scratch / "rclone.conf"
        self.config.write_text(
            "[drive]\ntype = local\n\n"
            f"[enc]\ntype = crypt\nremote = drive:{self.store}\n"
            f"password = {_obscure('e2e-password')}\npassword2 = {_obscure('e2e-salt')}\n"
        )
        self.override = override_settings(
            BACKUP_REMOTE="enc:",
            BACKUP_RCLONE_CONFIG=str(self.config),
            BACKUP_LOCAL_DIR=str(self.local),
            BACKUP_ENV_FILE=str(self.env_file),
            MEDIA_ROOT=str(self.media),
            BACKUP_KEEP_DAILY=30,
            BACKUP_KEEP_MONTHLY=12,
        )
        self.override.enable()
        self.addCleanup(self.override.disable)
        # Snapshot IDs have one-second resolution; tests run faster than that.
        ids = (f"20261116T2100{second:02d}Z" for second in itertools.count())
        patcher = mock.patch("backups.engine.new_snapshot_id", side_effect=lambda: next(ids))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, kind, **kwargs):
        run = BackupRun.objects.create(kind=kind, trigger=BackupRun.Trigger.COMMAND)
        engine.perform(run, **kwargs)
        run.refresh_from_db()
        return run

    def _ls(self, path):
        out = subprocess.run(
            ["rclone", "--config", str(self.config), "lsf", "-R", "--files-only", f"enc:{path}"],
            capture_output=True, text=True,
        ).stdout
        return sorted(out.split())

    def _cat(self, path):
        return subprocess.run(
            ["rclone", "--config", str(self.config), "cat", f"enc:{path}"],
            capture_output=True, check=True,
        ).stdout

    def test_backup_sends_database_media_and_env_off_the_server_encrypted(self):
        run = self._run(BackupRun.Kind.BACKUP)
        self.assertEqual(run.status, BackupRun.Status.SUCCEEDED, run.error + "\n" + run.log)

        db_name = connection.settings_dict["NAME"]
        dump = f"{db_name}-{run.snapshot}.dump"
        self.assertEqual(self._ls("db"), [dump, dump + ".sha256"])
        self.assertEqual(self._ls("env"), [f"{run.snapshot}.env"])
        self.assertEqual(self._ls("media/current"), ["images/cover.png", "notes.txt"])
        self.assertEqual(self._cat(f"env/{run.snapshot}.env"), b"SECRET_KEY=not-a-real-one\n")
        self.assertEqual(run.media_files, 2)
        self.assertEqual(run.media_uploaded, 2)
        self.assertGreater(run.db_bytes, 0)
        self.assertEqual([s["id"] for s in run.details["snapshots"]], [run.snapshot])
        self.assertTrue(run.details["remote"]["encrypted"])

        # The checksum file matches the dump, in sha256sum's format.
        digest, name = self._cat(f"db/{dump}.sha256").decode().split()
        self.assertEqual(name, dump)
        self.assertEqual(digest, engine.sha256_file(self.local / "db" / dump))

        # Nothing readable reached "Google Drive": names and contents are encrypted.
        stored = [p for p in self.store.rglob("*") if p.is_file()]
        self.assertTrue(stored)
        for path in stored:
            self.assertNotIn(b"SECRET_KEY", path.read_bytes())
            self.assertNotRegex(str(path), r"\.dump|\.env|cover|notes")

    def test_media_changes_are_mirrored_and_old_versions_archived(self):
        first = self._run(BackupRun.Kind.BACKUP)
        (self.media / "notes.txt").write_text("second, longer version")
        (self.media / "images" / "cover.png").unlink()
        (self.media / "new.txt").write_text("new")

        second = self._run(BackupRun.Kind.BACKUP)
        self.assertEqual(second.status, BackupRun.Status.SUCCEEDED, second.error)
        self.assertEqual(self._ls("media/current"), ["new.txt", "notes.txt"])
        self.assertEqual(
            self._ls(f"media/archive/{second.snapshot}"), ["images/cover.png", "notes.txt"]
        )
        self.assertEqual(self._cat(f"media/archive/{second.snapshot}/notes.txt"), b"first")
        self.assertEqual(second.media_uploaded, 2)
        self.assertEqual(second.media_archived, 2)
        self.assertNotEqual(first.snapshot, second.snapshot)

    def test_retention_prunes_old_snapshots_and_their_env_files(self):
        with self.settings(BACKUP_KEEP_DAILY=2, BACKUP_KEEP_MONTHLY=0, BACKUP_KEEP_LOCAL=1):
            runs = [self._run(BackupRun.Kind.BACKUP) for _ in range(3)]
        newest = [runs[2].snapshot, runs[1].snapshot]
        self.assertEqual(self._ls("env"), sorted(f"{s}.env" for s in newest))
        self.assertEqual(len(self._ls("db")), 4)
        self.assertEqual([s["id"] for s in runs[2].details["snapshots"]], newest)
        self.assertEqual(len(list((self.local / "db").glob("*.dump"))), 1)

    def test_old_media_archives_are_purged(self):
        self._run(BackupRun.Kind.BACKUP)
        (self.media / "notes.txt").unlink()
        archived = self._run(BackupRun.Kind.BACKUP)
        self.assertEqual(self._ls(f"media/archive/{archived.snapshot}"), ["notes.txt"])
        later = engine.snapshot_time(archived.snapshot) + timedelta(days=31)
        with mock.patch("backups.engine.timezone.now", return_value=later):
            self._run(BackupRun.Kind.BACKUP)
        self.assertEqual(self._ls("media/archive"), [])

    def test_verify_downloads_and_reads_back_the_newest_snapshot(self):
        backup = self._run(BackupRun.Kind.BACKUP)
        run = self._run(BackupRun.Kind.VERIFY)
        self.assertEqual(run.status, BackupRun.Status.SUCCEEDED, run.error + "\n" + run.log)
        self.assertEqual(run.snapshot, backup.snapshot)
        self.assertTrue(run.details["verify"]["env"])
        self.assertGreater(run.details["verify"]["objects"], 0)
        self.assertIn("pg_restore read all", run.log)
        self.assertEqual(list((self.local / "verify").iterdir()), [])  # download removed

    def test_verify_fails_when_the_dump_does_not_match_its_checksum(self):
        backup = self._run(BackupRun.Kind.BACKUP)
        dump = f"{connection.settings_dict['NAME']}-{backup.snapshot}.dump"
        subprocess.run(
            ["rclone", "--config", str(self.config), "rcat", f"enc:db/{dump}.sha256"],
            input=b"0" * 64 + b"  " + dump.encode() + b"\n", check=True, capture_output=True,
        )
        with self.assertRaises(engine.BackupError):
            self._run(BackupRun.Kind.VERIFY)
        run = BackupRun.objects.filter(kind=BackupRun.Kind.VERIFY).get()
        self.assertEqual(run.status, BackupRun.Status.FAILED)
        self.assertIn("is damaged", run.error)

    def test_verify_flags_media_missing_from_the_backup(self):
        self._run(BackupRun.Kind.BACKUP)
        subprocess.run(
            ["rclone", "--config", str(self.config), "deletefile", "enc:media/current/notes.txt"],
            check=True,
        )
        old = (timezone.now() - timedelta(days=1)).timestamp()
        os.utime(self.media / "notes.txt", (old, old))
        BackupRun.objects.filter(kind=BackupRun.Kind.BACKUP).update(started_at=timezone.now())
        with self.assertRaises(engine.BackupError) as raised:
            self._run(BackupRun.Kind.VERIFY)
        self.assertIn("notes.txt", str(raised.exception))

    def test_an_unencrypted_remote_is_refused_after_a_local_dump(self):
        with self.settings(BACKUP_REMOTE="drive:" + str(self.store)):
            with self.assertRaises(engine.BackupError):
                self._run(BackupRun.Kind.BACKUP)
        run = BackupRun.objects.get()
        self.assertIn("nothing was copied off it", run.error)
        self.assertIn("unencrypted", run.error)
        self.assertEqual(len(list((self.local / "db").glob("*.dump"))), 1)  # still dumped locally
        self.assertFalse(self.store.exists())

    def test_unencrypted_remote_when_allowed_leaves_out_the_env(self):
        with self.settings(BACKUP_REMOTE="drive:" + str(self.store), BACKUP_REQUIRE_ENCRYPTION=False):
            run = self._run(BackupRun.Kind.BACKUP)
        self.assertEqual(run.status, BackupRun.Status.SUCCEEDED, run.error)
        self.assertFalse((self.store / "env").exists())
        self.assertTrue((self.store / "media" / "current" / "notes.txt").exists())

    def test_an_empty_media_folder_never_empties_the_backup(self):
        self._run(BackupRun.Kind.BACKUP)
        shutil.rmtree(self.media)
        self.media.mkdir()
        with self.assertRaises(engine.BackupError) as raised:
            self._run(BackupRun.Kind.BACKUP)
        self.assertIn("is empty but the off-server copy is not", str(raised.exception))
        self.assertEqual(self._ls("media/current"), ["images/cover.png", "notes.txt"])

    def test_no_remote_still_dumps_locally_and_says_so(self):
        with self.settings(BACKUP_REMOTE=""):
            with self.assertRaises(engine.BackupError):
                self._run(BackupRun.Kind.BACKUP)
        run = BackupRun.objects.get()
        self.assertIn("BACKUP_REMOTE is not set", run.error)
        self.assertEqual(len(list((self.local / "db").glob("*.dump"))), 1)

    def test_setup_check_reports_every_part(self):
        run = self._run(BackupRun.Kind.CHECK)
        self.assertEqual(run.status, BackupRun.Status.SUCCEEDED, run.error)
        names = [check["name"] for check in run.details["checks"]]
        for expected in ("Local backup folder", "pg_dump", "rclone", "Encryption", "Connection",
                         "Storage", "Secrets (.env)", "Media folder"):
            self.assertIn(expected, names)
        self.assertTrue(all(check["state"] == "ok" for check in run.details["checks"]), run.details)

    def test_an_rclone_config_rclone_cannot_rewrite_is_explained(self):
        # rclone saves Google's refreshed token by writing beside the file.
        real_access, folder = os.access, str(self.config.parent)
        fake = lambda path, mode: False if str(path) == folder else real_access(path, mode)  # noqa: E731
        with mock.patch("backups.engine.os.access", side_effect=fake):
            with self.assertRaises(engine.BackupError) as raised:
                self._run(BackupRun.Kind.CHECK)
        self.assertIn("and its folder must be writable", str(raised.exception))

    def test_a_missing_remote_name_is_explained(self):
        with self.settings(BACKUP_REMOTE="nope:"):
            with self.assertRaises(engine.BackupError) as raised:
                self._run(BackupRun.Kind.CHECK)
        self.assertIn("no remote called 'nope'", str(raised.exception))

    def test_only_one_run_at_a_time(self):
        with engine.exclusive_lock():
            with self.assertRaises(engine.BackupError) as raised:
                self._run(BackupRun.Kind.CHECK)
        self.assertIn("Another backup", str(raised.exception))

    def test_a_run_left_running_by_a_dead_worker_is_marked_interrupted(self):
        stale = BackupRun.objects.create(
            kind=BackupRun.Kind.BACKUP, trigger=BackupRun.Trigger.SCHEDULED,
            status=BackupRun.Status.RUNNING, started_at=timezone.now(),
        )
        self._run(BackupRun.Kind.CHECK)
        stale.refresh_from_db()
        self.assertEqual(stale.status, BackupRun.Status.FAILED)
        self.assertIn("Interrupted", stale.error)

    def test_backup_command_prints_the_log(self):
        out = StringIO()
        call_command("backup", "--check", stdout=out)
        self.assertIn("✓ Encryption", out.getvalue())
        self.assertIn("succeeded", out.getvalue())


class HealthAlertTests(TestCase):
    """The twice-daily email (backups.tasks.check_backup_health)."""

    ADMINS = [("ops@example.org", "ops@example.org")]

    def _backup(self, hours_ago, status=BackupRun.Status.SUCCEEDED, error=""):
        finished = timezone.now() - timedelta(hours=hours_ago)
        return BackupRun.objects.create(
            kind=BackupRun.Kind.BACKUP, trigger=BackupRun.Trigger.SCHEDULED, status=status,
            started_at=finished - timedelta(minutes=2), finished_at=finished, error=error,
        )

    def _check(self):
        from backups.tasks import check_backup_health

        with self.settings(ADMINS=self.ADMINS, BACKUP_REMOTE="icarus-backup:"):
            return check_backup_health()

    def test_quiet_while_the_last_backup_is_fresh(self):
        self._backup(hours_ago=5)
        self.assertTrue(self._check())
        self.assertEqual(mail.outbox, [])

    def test_emails_once_the_last_backup_is_stale(self):
        self._backup(hours_ago=30)
        self._backup(hours_ago=6, status=BackupRun.Status.FAILED, error="token expired")
        self.assertFalse(self._check())
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertIn("Backups need attention", mail.outbox[0].subject)
        self.assertIn("30 hours ago", body)
        self.assertIn("token expired", body)  # why it is stale
        self.assertIn("/admin/backups/", body)

    def test_emails_when_nothing_ever_succeeded(self):
        self.assertFalse(self._check())
        self.assertIn("No backup has completed yet.", mail.outbox[0].body)

    def test_emails_when_the_remote_is_not_configured(self):
        from backups.tasks import check_backup_health

        self._backup(hours_ago=1)
        with self.settings(ADMINS=self.ADMINS, BACKUP_REMOTE=""):
            self.assertFalse(check_backup_health())
        self.assertIn("BACKUP_REMOTE is empty", mail.outbox[0].body)

    def test_a_failed_verification_is_worth_an_email(self):
        self._backup(hours_ago=1)
        BackupRun.objects.create(
            kind=BackupRun.Kind.VERIFY, trigger=BackupRun.Trigger.SCHEDULED,
            status=BackupRun.Status.FAILED, error="checksum mismatch",
        )
        self.assertFalse(self._check())
        self.assertIn("checksum mismatch", mail.outbox[0].body)

    def test_one_failed_run_with_a_fresh_backup_is_only_a_warning(self):
        self._backup(hours_ago=3)
        self._backup(hours_ago=1, status=BackupRun.Status.FAILED, error="network blip")
        self.assertTrue(self._check())
        self.assertEqual(mail.outbox, [])
        with self.settings(BACKUP_REMOTE="icarus-backup:"):
            self.assertIn("network blip", backup_health()["warnings"][0])

    def test_without_admins_it_fails_instead_of_sending_nothing(self):
        from backups.tasks import check_backup_health

        with self.settings(ADMINS=[]):
            with self.assertRaises(ImproperlyConfigured):
                check_backup_health()


class ScheduleTests(TestCase):
    def test_registers_three_cron_schedules_idempotently(self):
        from django_q.models import Schedule

        call_command("setup_backup_schedules", stdout=StringIO())
        first = {s.func: s.next_run for s in Schedule.objects.all()}
        call_command("setup_backup_schedules", stdout=StringIO())
        schedules = {s.func: s for s in Schedule.objects.all()}
        self.assertEqual(set(schedules), {
            "backups.tasks.scheduled_backup",
            "backups.tasks.scheduled_verify",
            "backups.tasks.check_backup_health",
        })
        for func, schedule in schedules.items():
            self.assertEqual(schedule.schedule_type, Schedule.CRON)
            self.assertEqual(schedule.repeats, -1)
            self.assertGreater(schedule.next_run, timezone.now())  # waits for its slot
            self.assertEqual(schedule.next_run, first[func])       # a re-run keeps it
        self.assertEqual(schedules["backups.tasks.scheduled_backup"].cron, settings.BACKUP_SCHEDULE_CRON)

        call_command("setup_backup_schedules", "--delete", stdout=StringIO())
        self.assertFalse(Schedule.objects.exists())

    def test_a_new_backup_time_moves_the_next_run(self):
        from django_q.models import Schedule

        call_command("setup_backup_schedules", stdout=StringIO())
        with self.settings(BACKUP_SCHEDULE_CRON="0 20 * * *"):
            call_command("setup_backup_schedules", stdout=StringIO())
        schedule = Schedule.objects.get(func="backups.tasks.scheduled_backup")
        self.assertEqual(schedule.cron, "0 20 * * *")
        self.assertEqual((schedule.next_run.hour, schedule.next_run.minute), (20, 0))

    def test_rejects_a_broken_cron_expression(self):
        from django.core.management.base import CommandError

        with self.settings(BACKUP_SCHEDULE_CRON="every night"):
            with self.assertRaises(CommandError):
                call_command("setup_backup_schedules", stdout=StringIO())

    def test_the_web_container_registers_the_schedules_on_start(self):
        dockerfile = (settings.BASE_DIR / "Dockerfile").read_text()
        self.assertIn("python manage.py setup_backup_schedules;", dockerfile)


class DeploymentWiringTests(TestCase):
    """The pieces outside Python that the backups depend on."""

    def test_the_app_image_and_restore_tools_pin_the_same_rclone(self):
        dockerfile = (settings.BASE_DIR / "Dockerfile").read_text()
        compose = (settings.BASE_DIR / "docker-compose.yml").read_text()
        image = re.search(r"COPY --from=(rclone/rclone:[\d.]+)", dockerfile).group(1)
        self.assertIn(f"image: {image}", compose)

    def test_the_app_image_has_a_pg_dump_for_the_database_server(self):
        dockerfile = (settings.BASE_DIR / "Dockerfile").read_text()
        compose = (settings.BASE_DIR / "docker-compose.yml").read_text()
        server = re.search(r"pgvector/pgvector:pg(\d+)", compose).group(1)
        self.assertIn(f"postgresql-client-{server}", dockerfile)

    def _compose(self):
        import yaml  # always installed: langchain-core and transformers need it

        return yaml.safe_load((settings.BASE_DIR / "docker-compose.yml").read_text())["services"]

    def test_only_the_worker_gets_the_rclone_config(self):
        # The Drive token and the encryption keys stay out of the internet-facing web.
        services = self._compose()
        holders = sorted(
            name for name, service in services.items()
            if any("/opt/icarus/shared/rclone" in str(volume) for volume in service.get("volumes", []))
        )
        self.assertEqual(holders, ["backup-tools", "qcluster"])
        self.assertEqual(services["qcluster"]["environment"]["BACKUP_RCLONE_CONFIG"], "/config/rclone/rclone.conf")

    def test_restore_tools_never_start_with_the_stack(self):
        tools = self._compose()["backup-tools"]
        self.assertEqual(tools["profiles"], ["tools"])  # `docker compose up -d` skips it
        self.assertEqual(tools["user"], "1000:1000")    # the app image's wagtail user
        dockerfile = (settings.BASE_DIR / "Dockerfile").read_text()
        self.assertIn("useradd -m -u 1000 wagtail", dockerfile)


class DashboardTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(phone_number="+918800990001", name="Root", password="pw")
        # Staff, like a board member: is_staff alone must not open the backups.
        self.editor = User.objects.create_user(phone_number="+918800990002", name="Editor", is_staff=True)
        self.editor.user_permissions.add(Permission.objects.get(codename="access_admin"))
        self.url = reverse("backups_dashboard")

    def _login(self, user):
        self.client.force_login(user)

    def test_needs_the_manage_backups_permission(self):
        self.assertIn("/admin/login/", self.client.get(self.url)["Location"])
        # Wagtail answers a denied admin page with a redirect home and a message.
        self._login(self.editor)
        home = reverse("wagtailadmin_home")
        self.assertRedirects(self.client.get(self.url), home, fetch_redirect_response=False)
        response = self.client.post(reverse("backups_start", args=["backup"]))
        self.assertRedirects(response, home, fetch_redirect_response=False)
        self.assertFalse(BackupRun.objects.exists())
        self.assertRedirects(
            self.client.get(reverse("backups_status"), {"run": 1}), home, fetch_redirect_response=False
        )

        group = Group.objects.create(name="Ops")
        group.permissions.add(Permission.objects.get(codename="manage_backups"))
        self.editor.groups.add(group)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_the_permission_can_be_granted_in_wagtails_group_editor(self):
        from wagtail import hooks

        offered = [p for fn in hooks.get_hooks("register_permissions") for p in fn()]
        self.assertIn("manage_backups", [p.codename for p in offered])

    def test_menu_item_only_for_those_who_may_open_it(self):
        from django.test import RequestFactory

        from backups.wagtail_hooks import register_backups_menu_item

        item = register_backups_menu_item()
        request = RequestFactory().get("/admin/")
        request.user = self.editor
        self.assertFalse(item.is_shown(request))
        request.user = self.admin
        self.assertTrue(item.is_shown(request))

    def test_empty_dashboard_explains_what_is_missing(self):
        self._login(self.admin)
        response = self.client.get(self.url)
        self.assertContains(response, "Backups need attention")
        self.assertContains(response, "No backup has completed yet.")
        self.assertContains(response, "Nothing checked yet")
        self.assertContains(response, "Not scheduled")

    def test_dashboard_shows_the_last_backup_and_its_snapshots(self):
        call_command("setup_backup_schedules", stdout=StringIO())
        BackupRun.objects.create(
            kind=BackupRun.Kind.BACKUP, trigger=BackupRun.Trigger.SCHEDULED,
            status=BackupRun.Status.SUCCEEDED, snapshot="20261116T210000Z",
            started_at=timezone.now() - timedelta(hours=2), finished_at=timezone.now() - timedelta(hours=2),
            db_bytes=52_000_000, media_files=1234, media_bytes=4_500_000_000,
            media_uploaded=12, media_archived=3,
            details={
                "checks": [{"name": "Encryption", "state": "ok", "detail": "crypt remote"}],
                "remote": {"remote": "icarus-backup:", "type": "crypt", "encrypted": True,
                           "usage": {"total": 100 * 1024**3, "used": 25 * 1024**3, "free": 75 * 1024**3}},
                "snapshots": [
                    {"id": "20261116T210000Z", "file": "icarus-20261116T210000Z.dump", "bytes": 52_000_000, "monthly": False, "env": True},
                    {"id": "20261001T210000Z", "file": "icarus-20261001T210000Z.dump", "bytes": 50_000_000, "monthly": True, "env": True},
                ],
            },
        )
        self._login(self.admin)
        with self.settings(BACKUP_REMOTE="icarus-backup:"):
            response = self.client.get(self.url)
        self.assertContains(response, "Backups are healthy")
        self.assertContains(response, "./restore.sh db 20261116T210000Z")
        self.assertContains(response, "./restore.sh db 20261001T210000Z")
        self.assertContains(response, "monthly")
        self.assertContains(response, 'name="snapshot" value="20261001T210000Z"')  # verify an old one
        self.assertContains(response, "1,234 files")
        self.assertContains(response, "75.0\xa0GB free")  # filesizeformat's non-breaking space
        self.assertContains(response, "crypt remote")
        self.assertNotContains(response, "Not scheduled")

    def test_back_up_now_queues_a_task_for_the_worker(self):
        self._login(self.admin)
        with mock.patch("backups.views.async_task") as queue:
            response = self.client.post(reverse("backups_start", args=["backup"]))
        self.assertRedirects(response, self.url)
        run = BackupRun.objects.get()
        self.assertEqual((run.kind, run.trigger, run.status), ("backup", "manual", "queued"))
        self.assertEqual(run.requested_by, self.admin)
        queue.assert_called_once()
        self.assertEqual(queue.call_args.args[:3], ("backups.tasks.perform_run", run.pk, None))

        # While it is queued, the page shows it live and the buttons are off.
        response = self.client.get(self.url)
        self.assertContains(response, f"Backup #{run.pk} in progress")
        self.assertContains(response, f'data-bk-live="{reverse("backups_status")}?run={run.pk}"')
        for button in ("Back up now", "Verify latest backup", "Check setup"):
            self.assertContains(response, f" disabled>{button}</button>")

    def test_verify_can_target_one_snapshot(self):
        self._login(self.admin)
        with mock.patch("backups.views.async_task") as queue:
            self.client.post(reverse("backups_start", args=["verify"]), {"snapshot": "20261116T210000Z"})
        self.assertEqual(queue.call_args.args[2], "20261116T210000Z")
        with mock.patch("backups.views.async_task") as queue:
            BackupRun.objects.update(status=BackupRun.Status.SUCCEEDED)
            self.client.post(reverse("backups_start", args=["verify"]), {"snapshot": "../etc"})
        queue.assert_not_called()

    def test_only_one_run_at_a_time_from_the_dashboard(self):
        BackupRun.objects.create(kind=BackupRun.Kind.BACKUP, trigger=BackupRun.Trigger.SCHEDULED)
        self._login(self.admin)
        with mock.patch("backups.views.async_task") as queue:
            self.client.post(reverse("backups_start", args=["check"]))
        queue.assert_not_called()
        self.assertEqual(BackupRun.objects.count(), 1)

    def test_start_is_post_only_and_knows_its_kinds(self):
        self._login(self.admin)
        self.assertEqual(self.client.get(reverse("backups_start", args=["backup"])).status_code, 405)
        self.assertEqual(self.client.post(reverse("backups_start", args=["restore"])).status_code, 404)

    def test_queued_task_runs_the_row_the_dashboard_made(self):
        from backups import tasks

        run = BackupRun.objects.create(kind=BackupRun.Kind.CHECK, trigger=BackupRun.Trigger.MANUAL)
        with mock.patch("backups.tasks.engine.perform") as perform:
            tasks.perform_run(run.pk)
        self.assertEqual(perform.call_args.args[0].pk, run.pk)
        self.assertIsNotNone(perform.call_args.kwargs["deadline"])

        # A redelivered task does not run the same row twice.
        BackupRun.objects.filter(pk=run.pk).update(status=BackupRun.Status.SUCCEEDED)
        with mock.patch("backups.tasks.engine.perform") as perform:
            tasks.perform_run(run.pk)
        perform.assert_not_called()

    def test_status_endpoint_feeds_the_live_log(self):
        run = BackupRun.objects.create(
            kind=BackupRun.Kind.BACKUP, trigger=BackupRun.Trigger.MANUAL,
            status=BackupRun.Status.RUNNING, log="10:00:00  Dumping database …",
        )
        self._login(self.admin)
        data = self.client.get(reverse("backups_status"), {"run": run.pk}).json()
        self.assertEqual(data["status"], "running")
        self.assertTrue(data["active"])
        self.assertIn("Dumping database", data["log"])
        self.assertEqual(self.client.get(reverse("backups_status"), {"run": "x"}).status_code, 404)

    def test_run_detail_shows_checks_errors_and_the_log(self):
        run = BackupRun.objects.create(
            kind=BackupRun.Kind.BACKUP, trigger=BackupRun.Trigger.SCHEDULED,
            status=BackupRun.Status.FAILED, error="rclone exit 7",
            started_at=timezone.now() - timedelta(seconds=75), finished_at=timezone.now(),
            log="21:00:01  ✗ Connection: token expired",
            details={"checks": [{"name": "Connection", "state": "fail", "detail": "token expired"}]},
        )
        self._login(self.admin)
        response = self.client.get(reverse("backups_run", args=[run.pk]))
        self.assertContains(response, "rclone exit 7")
        self.assertContains(response, "✗ Fix")
        self.assertContains(response, "1m 15s")
        self.assertContains(response, "Connection: token expired")
        self.assertNotContains(response, "data-bk-live")  # finished: no polling
