"""
The off-server backup engine: everything a backup, a verification or a setup
check actually does. Runs in the qcluster worker (backups.tasks) or from
``manage.py backup``; the web container never runs it, and never needs the
rclone config that holds the Google Drive token and the encryption keys.

What a backup sends to BACKUP_REMOTE (an rclone remote, normally a ``crypt``
remote wrapping Google Drive, so every name and byte is encrypted before it
leaves the server):

    db/<db>-<snapshot>.dump           pg_dump custom-format archive
    db/<db>-<snapshot>.dump.sha256    its checksum; written last, so a dump
                                      without one is an unfinished upload
    env/<snapshot>.env                the server's .env (crypt remotes only)
    media/current/...                 rsync-style mirror of MEDIA_ROOT
    media/archive/<snapshot>/...      files that run deleted or replaced in
                                      media/current, kept for
                                      BACKUP_MEDIA_ARCHIVE_DAYS

<snapshot> is the UTC start time, e.g. 20261116T210000Z. restore.sh (in the
repository root) reads exactly this layout: keep the two in step.
"""
import ctypes
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
import traceback
from collections import deque
from contextlib import contextmanager
from datetime import timedelta, timezone as dt_timezone
from pathlib import Path

from django.conf import settings
from django.db import connections
from django.utils import timezone

from backups.models import SNAPSHOT_FORMAT, BackupRun, snapshot_time

DB_DIR = "db"
ENV_DIR = "env"
MEDIA_CURRENT = "media/current"
MEDIA_ARCHIVE = "media/archive"

DUMP_RE = re.compile(r"^(?P<name>.+)-(?P<id>\d{8}T\d{6}Z)\.dump$")
ENV_RE = re.compile(r"^(?P<id>\d{8}T\d{6}Z)\.env$")
SNAPSHOT_RE = re.compile(r"^\d{8}T\d{6}Z$")

# rclone exit code for "stopped by --max-duration".
RCLONE_DURATION_EXCEEDED = 10
# rclone exit code for "directory not found" (e.g. db/ before the first upload).
RCLONE_DIR_NOT_FOUND = 3

# Logged, streamed rclone commands: JSON lines we can parse, warnings and
# errors only, plus a progress summary every 30 s and one at the end.
RCLONE_STREAM_FLAGS = [
    "--use-json-log", "--log-level", "NOTICE",
    "--stats", "30s", "--stats-log-level", "NOTICE",
]

# Stop media uploads this long before Django-Q's task timeout, leaving time to
# list the remote, prune it and save the run.
DEADLINE_MARGIN = timedelta(minutes=15)

# Warn when the remote has less than this free.
LOW_REMOTE_SPACE = 2 * 1024**3


class BackupError(Exception):
    """A failure with a message an admin can act on; shown on the dashboard."""


# ── Small helpers ─────────────────────────────────────────────────────────


def human_size(num_bytes):
    """1536 -> '1.5 KB' (binary units, like Django's filesizeformat)."""
    if num_bytes is None:
        return "?"
    size = float(num_bytes)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} bytes" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024


def human_duration(seconds):
    seconds = int(seconds or 0)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def new_snapshot_id(now=None):
    return (now or timezone.now()).astimezone(dt_timezone.utc).strftime(SNAPSHOT_FORMAT)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def remote_path(*parts):
    """BACKUP_REMOTE joined with `parts`: remote_path("db", "x") -> "icarus-backup:db/x"."""
    base = settings.BACKUP_REMOTE
    tail = "/".join(part.strip("/") for part in parts if part)
    if not tail:
        return base
    if not base or base.endswith((":", "/")):
        return base + tail
    return f"{base}/{tail}"


def remote_name():
    """The rclone remote BACKUP_REMOTE names ("icarus-backup"), or None for a plain path."""
    remote = settings.BACKUP_REMOTE
    if ":" not in remote:
        return None
    return remote.split(":", 1)[0]


def parse_listremotes(output):
    """`rclone listremotes --long` -> {"icarus-backup": "crypt", "gdrive": "drive"}."""
    remotes = {}
    for line in output.splitlines():
        name, sep, kind = line.partition(":")
        if sep and name.strip():
            remotes[name.strip()] = kind.strip()
    return remotes


def plan_retention(snapshot_ids, keep_daily, keep_monthly):
    """
    Which snapshots to keep: the newest `keep_daily`, plus the first snapshot
    of each of the newest `keep_monthly` calendar months.

    Counted in snapshots, not days, so a month of failed backups never ages
    the last good ones out. Returns (keep, monthly), two sets of IDs.
    """
    newest_first = sorted(set(snapshot_ids), reverse=True)
    keep = set(newest_first[: max(keep_daily, 0)])
    first_of_month = {}
    for snapshot_id in reversed(newest_first):  # oldest first
        first_of_month.setdefault(snapshot_id[:6], snapshot_id)
    monthly = set(sorted(first_of_month.values(), reverse=True)[: max(keep_monthly, 0)])
    return keep | monthly, monthly


def _die_with_parent():
    """
    preexec_fn: have Linux SIGTERM the child if the worker dies.

    Django-Q kills a worker that outlives its task timeout; without this an
    rclone upload it started would carry on, orphaned, alongside the next run.
    """
    try:
        ctypes.CDLL(None).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    except (OSError, AttributeError):  # not Linux (a developer's Mac)
        pass


def _display(cmd):
    return " ".join(shlex.quote(str(part)) for part in cmd)


def _tail(text, lines=8):
    return "\n".join((text or "").strip().splitlines()[-lines:])


def _capture(cmd, *, env=None, timeout=600, what=None, ok_codes=(0,)):
    """Run a short command; return (exit code, stdout). Raise BackupError on failure."""
    what = what or Path(str(cmd[0])).name
    try:
        result = subprocess.run(
            [str(part) for part in cmd],
            capture_output=True, text=True, errors="replace",
            env=env, timeout=timeout, preexec_fn=_die_with_parent,
        )
    except FileNotFoundError as exc:
        raise BackupError(f"{Path(str(cmd[0])).name} is not installed in this container.") from exc
    except subprocess.TimeoutExpired as exc:
        raise BackupError(f"{what} did not finish within {timeout} s.") from exc
    if result.returncode not in ok_codes:
        detail = _tail(result.stderr) or _tail(result.stdout) or "no output"
        raise BackupError(f"{what} failed (exit {result.returncode}): {detail}")
    return result.returncode, result.stdout


def rclone(*args):
    """An rclone command line using the configured binary and config file."""
    cmd = [settings.BACKUP_RCLONE_BINARY]
    if settings.BACKUP_RCLONE_CONFIG:
        cmd += ["--config", settings.BACKUP_RCLONE_CONFIG]
    return cmd + [str(arg) for arg in args]


def _lsf(path, *flags):
    """Names directly inside a remote folder; [] when it doesn't exist yet."""
    code, out = _capture(
        rclone("lsf", *flags, path), what=f"Listing {path}", timeout=600,
        ok_codes=(0, RCLONE_DIR_NOT_FOUND),
    )
    if code == RCLONE_DIR_NOT_FOUND:
        return []
    return [line for line in out.splitlines() if line]


# ── The run log ───────────────────────────────────────────────────────────


class Recorder:
    """
    Collects a run's log and saves it to the row every few seconds, so the
    dashboard can show a long upload's progress while it happens. Keeps the
    first and the last lines when a run logs more than it can store.
    """

    HEAD_LINES = 80
    TAIL_LINES = 400
    FLUSH_SECONDS = 5

    def __init__(self, run, echo=None):
        self.run = run
        self.echo = echo
        self._head = []
        self._tail = deque(maxlen=self.TAIL_LINES)
        self._dropped = 0
        self._last_flush = 0.0

    def log(self, message=""):
        stamp = timezone.now().strftime("%H:%M:%S")
        for line in str(message).splitlines() or [""]:
            entry = f"{stamp}  {line}"
            if self.echo:
                self.echo(entry)
            if len(self._head) < self.HEAD_LINES:
                self._head.append(entry)
                continue
            if len(self._tail) == self._tail.maxlen:
                self._dropped += 1
            self._tail.append(entry)
        self.flush()

    def text(self):
        lines = list(self._head)
        if self._dropped:
            lines.append(f"… {self._dropped} lines left out …")
        lines.extend(self._tail)
        return "\n".join(lines)

    def flush(self, force=False):
        now = time.monotonic()
        if force or now - self._last_flush >= self.FLUSH_SECONDS:
            self.run.log = self.text()
            BackupRun.objects.filter(pk=self.run.pk).update(log=self.run.log)
            self._last_flush = now


class RcloneProgress:
    """Turns rclone's JSON log lines into readable ones; keeps the last stats."""

    def __init__(self, verb="transferred"):
        self.verb = verb
        self.stats = None

    def __call__(self, line):
        try:
            entry = json.loads(line)
        except ValueError:
            return line
        if not isinstance(entry, dict):
            return line
        if "stats" in entry:
            self.stats = entry["stats"]
            return self.describe(self.stats)
        level = str(entry.get("level", "")).upper()
        message = str(entry.get("msg", "")).strip()
        subject = entry.get("object")
        return f"{level}: {subject}: {message}" if subject else f"{level}: {message}"

    def describe(self, stats):
        text = (
            f"{human_size(stats.get('bytes', 0))} of {human_size(stats.get('totalBytes', 0))} "
            f"{self.verb}, {stats.get('transfers', 0)}/{stats.get('totalTransfers', 0)} files"
        )
        if stats.get("renames"):
            text += f", {stats['renames']} moved to the archive"
        if stats.get("errors"):
            text += f", {stats['errors']} errors"
        if stats.get("eta"):
            text += f", about {human_duration(stats['eta'])} left"
        return text


def _run_streamed(rec, cmd, *, env=None, formatter=None):
    """Run a long command, logging its output as it arrives. Returns the exit code."""
    rec.log("$ " + _display(cmd))
    try:
        proc = subprocess.Popen(
            [str(part) for part in cmd],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace", env=env, preexec_fn=_die_with_parent,
        )
    except FileNotFoundError as exc:
        raise BackupError(f"{Path(str(cmd[0])).name} is not installed in this container.") from exc
    with proc:
        for raw in proc.stdout:
            line = raw.rstrip()
            if line and formatter:
                line = formatter(line)
            if line:
                rec.log("  " + line)
        code = proc.wait()
    rec.flush(force=True)
    return code


def _rclone_streamed(rec, *args, formatter=None):
    return _run_streamed(rec, rclone(*args, *RCLONE_STREAM_FLAGS), formatter=formatter)


# ── Setup checks ──────────────────────────────────────────────────────────


OK, WARN, FAIL = "ok", "warn", "fail"


class Checks(list):
    """Setup check results, logged as they are made, saved on the run."""

    def __init__(self, rec):
        super().__init__()
        self.rec = rec

    def add(self, name, state, detail):
        self.append({"name": name, "state": state, "detail": detail})
        self.rec.log({OK: "✓", WARN: "!", FAIL: "✗"}[state] + f" {name}: {detail}")
        return state != FAIL

    def failures(self):
        return [check for check in self if check["state"] == FAIL]

    def require(self):
        failed = self.failures()
        if failed:
            raise BackupError(" ".join(f"{check['name']}: {check['detail']}" for check in failed))


def local_dir(*parts):
    path = Path(settings.BACKUP_LOCAL_DIR).joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _db_settings():
    return connections["default"].settings_dict


def _pg_env():
    """libpq environment for pg_dump: the app's own database connection."""
    db = _db_settings()
    env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    values = {
        "PGHOST": db.get("HOST"),
        "PGPORT": db.get("PORT"),
        "PGUSER": db.get("USER"),
        "PGPASSWORD": db.get("PASSWORD"),
        "PGDATABASE": db.get("NAME"),
    }
    env.update({key: str(value) for key, value in values.items() if value})
    env.update(PGCONNECT_TIMEOUT="30", PGAPPNAME="icarus-backup")
    return env


def _server_major():
    connection = connections["default"]
    connection.ensure_connection()
    return connection.pg_version // 10000


def check_local(checks):
    """The local half: a writable folder for the dump, and a new enough pg_dump."""
    try:
        folder = local_dir()
        probe = folder / ".write-test"
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        checks.add(
            "Local backup folder", FAIL,
            f"{settings.BACKUP_LOCAL_DIR} is not writable by uid {os.getuid()} ({exc.strerror or exc}).",
        )
    else:
        free = shutil.disk_usage(folder).free
        checks.add("Local backup folder", OK, f"{folder}, {human_size(free)} free on its disk.")

    try:
        _, out = _capture(["pg_dump", "--version"], what="pg_dump --version")
    except BackupError as exc:
        checks.add("pg_dump", FAIL, str(exc))
        return
    match = re.search(r"\)\s*(\d+)", out)
    client_major = int(match.group(1)) if match else 0
    server_major = _server_major()
    if client_major < server_major:
        checks.add(
            "pg_dump", FAIL,
            f"{out.strip()} cannot dump a PostgreSQL {server_major} server: "
            f"it needs postgresql-client-{server_major} or newer (see the Dockerfile).",
        )
    else:
        checks.add("pg_dump", OK, f"{out.strip()}, server is PostgreSQL {server_major}.")


def check_remote(checks):
    """
    The off-server half: rclone, its config, the remote's type (encrypted or
    not), that it answers, and how full it is. Returns what it learned.
    """
    info = {"remote": settings.BACKUP_REMOTE, "type": None, "encrypted": False, "usage": None}
    if not settings.BACKUP_REMOTE:
        checks.add(
            "Remote", FAIL,
            "BACKUP_REMOTE is not set in the server's .env, so nothing is copied off "
            "the server. Setup: backups/README.md.",
        )
        return info

    try:
        _, out = _capture(rclone("version"), what="rclone version")
    except BackupError as exc:
        checks.add("rclone", FAIL, str(exc))
        return info
    checks.add("rclone", OK, out.splitlines()[0] if out else "installed")

    config = settings.BACKUP_RCLONE_CONFIG
    if config:
        if not os.path.isfile(config):
            checks.add(
                "rclone config", FAIL,
                f"{config} does not exist. Create it with `rclone config` (backups/README.md, step 2).",
            )
            return info
        # rclone saves a refreshed token by writing a new file beside the old
        # one and renaming it, so the folder must be writable too.
        if not (os.access(config, os.R_OK | os.W_OK) and os.access(os.path.dirname(config) or ".", os.W_OK)):
            checks.add(
                "rclone config", FAIL,
                f"{config} and its folder must be writable by uid {os.getuid()}: rclone saves "
                "Google's refreshed access tokens to it. On the host: "
                "sudo chown -R 1000:1000 /opt/icarus/shared/rclone",
            )
            return info
        checks.add("rclone config", OK, config)

    name = remote_name()
    if name is None:
        kind = "local"
    else:
        try:
            _, out = _capture(rclone("listremotes", "--long"), what="rclone listremotes")
        except BackupError as exc:
            checks.add("Remote", FAIL, str(exc))
            return info
        remotes = parse_listremotes(out)
        if name not in remotes:
            checks.add(
                "Remote", FAIL,
                f"rclone.conf has no remote called '{name}' (it has: {', '.join(remotes) or 'none'}).",
            )
            return info
        kind = remotes[name]
    info["type"] = kind
    info["encrypted"] = kind == "crypt"

    if info["encrypted"]:
        checks.add(
            "Encryption", OK,
            f"'{name}' is a crypt remote: file names and contents are encrypted before they leave the server.",
        )
    elif settings.BACKUP_REQUIRE_ENCRYPTION:
        checks.add(
            "Encryption", FAIL,
            f"'{name or settings.BACKUP_REMOTE}' is a {kind} remote, so readers' personal data would be "
            "stored unencrypted. Point BACKUP_REMOTE at a crypt remote that wraps it "
            "(backups/README.md, step 2), or set BACKUP_REQUIRE_ENCRYPTION=False.",
        )
        return info
    else:
        checks.add(
            "Encryption", WARN,
            f"'{name or settings.BACKUP_REMOTE}' is a {kind} remote: backups are stored unencrypted, "
            "and the .env is left out.",
        )

    try:
        _capture(rclone("mkdir", remote_path()), what="Connecting to the remote", timeout=180)
    except BackupError as exc:
        checks.add(
            "Connection", FAIL,
            f"{exc} If Google reports an expired or revoked token, reconnect: "
            "backups/README.md, 'Reconnecting Google Drive'.",
        )
        return info
    checks.add("Connection", OK, f"{settings.BACKUP_REMOTE} answers.")

    try:
        _, out = _capture(rclone("about", "--json", remote_path()), what="rclone about", timeout=180)
        usage = json.loads(out)
    except (BackupError, ValueError):
        checks.add("Storage", WARN, "The remote did not report its storage usage.")
        return info
    info["usage"] = usage
    if not any(usage.get(key) is not None for key in ("used", "free", "total")):
        checks.add("Storage", OK, "The remote does not report its storage usage.")
        return info
    free = usage.get("free")
    summary = f"{human_size(usage.get('used'))} used"
    if usage.get("total"):
        summary += f" of {human_size(usage['total'])}"
    if free is not None:
        summary += f", {human_size(free)} free"
    if usage.get("trashed"):
        summary += f" ({human_size(usage['trashed'])} in the Drive trash)"
    checks.add("Storage", WARN if free is not None and free < LOW_REMOTE_SPACE else OK, summary + ".")
    return info


def check_env_file(checks, encrypted):
    """The .env to include in each snapshot, or None (with the reason logged)."""
    path = settings.BACKUP_ENV_FILE
    name = "Secrets (.env)"
    if not path:
        checks.add(name, WARN, "BACKUP_ENV_FILE is not set: the .env is not backed up. Keep it in a password manager.")
        return None
    path = Path(path)
    if not path.is_file():
        checks.add(name, WARN, f"{path} is not mounted in this container: the .env is not backed up.")
        return None
    if not os.access(path, os.R_OK):
        checks.add(
            name, WARN,
            f"{path} is not readable by uid {os.getuid()}, so it is not backed up. On the host: "
            "sudo chgrp 1000 /opt/icarus/shared/.env && sudo chmod 640 /opt/icarus/shared/.env",
        )
        return None
    if not encrypted:
        checks.add(name, WARN, "Left out: the remote is not encrypted.")
        return None
    checks.add(name, OK, "Included in every snapshot, encrypted.")
    return path


def check_media(checks):
    root = Path(settings.MEDIA_ROOT)
    if root.is_dir():
        return checks.add("Media folder", OK, str(root))
    return checks.add("Media folder", FAIL, f"MEDIA_ROOT ({root}) does not exist in this container.")


# ── Backing up ────────────────────────────────────────────────────────────


def _require_space(folder):
    """Refuse to start a dump that could fill the server's disk."""
    previous = (
        BackupRun.objects.filter(kind=BackupRun.Kind.BACKUP, db_bytes__isnull=False)
        .order_by("-created_at").values_list("db_bytes", flat=True).first()
    ) or 0
    needed = 2 * previous + 512 * 1024**2
    free = shutil.disk_usage(folder).free
    if free < needed:
        raise BackupError(
            f"Only {human_size(free)} free on the disk holding {folder}; a dump needs about "
            f"{human_size(needed)}. Free some space first (`docker system df` is a good start)."
        )


def _dump_entries(path):
    _, toc = _capture(["pg_restore", "--list", path], what="Reading the dump's table of contents")
    return sum(1 for line in toc.splitlines() if line.strip() and not line.startswith(";"))


def dump_database(rec, snapshot_id):
    """pg_dump to the local folder, check it reads back, and checksum it."""
    db_name = _db_settings()["NAME"]
    folder = local_dir(DB_DIR)
    _require_space(folder)
    final = folder / f"{db_name}-{snapshot_id}.dump"
    partial = folder / f"{final.name}.partial"

    rec.log(f"Dumping database {db_name} …")
    code = _run_streamed(rec, [
        "pg_dump", "--format=custom", "--no-password",
        # Give up rather than queue behind a migration's exclusive lock forever.
        "--lock-wait-timeout=5min",
        f"--file={partial}",
    ], env=_pg_env())
    if code != 0:
        partial.unlink(missing_ok=True)
        raise BackupError(f"pg_dump failed (exit {code}); see the log.")

    entries = _dump_entries(partial)
    if not entries:
        partial.unlink(missing_ok=True)
        raise BackupError("pg_dump wrote an empty archive.")
    partial.rename(final)
    digest = sha256_file(final)
    # sha256sum's format, so `sha256sum -c` checks a downloaded copy too.
    sidecar = folder / f"{final.name}.sha256"
    sidecar.write_text(f"{digest}  {final.name}\n")
    rec.log(f"Database dumped: {final.name}, {human_size(final.stat().st_size)}, {entries} objects.")
    return final


def prune_local(rec, current):
    """Keep the newest BACKUP_KEEP_LOCAL dumps (always including `current`)."""
    folder = local_dir(DB_DIR)
    for leftover in folder.glob("*.partial"):
        leftover.unlink(missing_ok=True)
    dumps = sorted(
        (path for path in folder.glob("*.dump") if DUMP_RE.match(path.name)),
        key=lambda path: DUMP_RE.match(path.name)["id"], reverse=True,
    )
    keep = max(settings.BACKUP_KEEP_LOCAL, 1)
    for old in dumps[keep:]:
        if old == current:
            continue
        old.unlink(missing_ok=True)
        (folder / f"{old.name}.sha256").unlink(missing_ok=True)
        rec.log(f"Removed old local copy {old.name}.")


def upload_dump(rec, dump):
    """The dump first, then its checksum: a snapshot counts once the checksum exists."""
    for path in (dump, dump.with_name(dump.name + ".sha256")):
        code = _rclone_streamed(
            rec, "copyto", path, remote_path(DB_DIR, path.name),
            formatter=RcloneProgress("uploaded"),
        )
        if code != 0:
            raise BackupError(f"Uploading {path.name} failed (rclone exit {code}); see the log.")
    rec.log(f"Uploaded {dump.name} to {remote_path(DB_DIR)}.")


def upload_env(rec, snapshot_id, env_file):
    target = remote_path(ENV_DIR, f"{snapshot_id}.env")
    _capture(rclone("copyto", env_file, target), what="Uploading the .env", timeout=300)
    rec.log(f"Uploaded the .env as {target}.")


def _has_any_file(root):
    for _, _, files in os.walk(root):
        if files:
            return True
    return False


def _exclude_flags():
    flags = []
    for pattern in settings.BACKUP_MEDIA_EXCLUDE:
        flags += ["--exclude", pattern]
    return flags


def sync_media(rec, run, snapshot_id, deadline=None):
    """
    rsync-style mirror of MEDIA_ROOT to media/current. Only new and changed
    files are uploaded. Files deleted or replaced on the server are not lost
    off it either: rclone moves them into media/archive/<snapshot> first.
    """
    root = Path(settings.MEDIA_ROOT)
    if not root.is_dir():
        raise BackupError(f"MEDIA_ROOT ({root}) does not exist in this container.")
    if not _has_any_file(root) and _lsf(remote_path(MEDIA_CURRENT), "--max-depth", "1"):
        raise BackupError(
            f"{root} is empty but the off-server copy is not. Not syncing: that would move every "
            "file into the archive. Is the media volume mounted?"
        )

    args = [
        "sync", root, remote_path(MEDIA_CURRENT),
        "--backup-dir", remote_path(MEDIA_ARCHIVE, snapshot_id),
        "--fast-list", "--transfers", "4", "--checkers", "8",
        "--drive-chunk-size", "32M",
        *_exclude_flags(),
    ]
    if deadline is not None:
        budget = int(deadline - time.monotonic())
        if budget < 60:
            raise BackupError("No time left in this run for the media upload; the next run does it.")
        args += ["--max-duration", f"{budget}s"]

    rec.log("Syncing media …")
    progress = RcloneProgress("uploaded")
    code = _rclone_streamed(rec, *args, formatter=progress)
    stats = progress.stats or {}
    run.media_uploaded = stats.get("transfers")
    run.media_archived = stats.get("renames")
    if code == RCLONE_DURATION_EXCEEDED:
        raise BackupError(
            "The media upload ran out of time before it finished. Nothing is lost: the next run "
            "carries on where this one stopped (the first upload of a large media folder can "
            "take several runs)."
        )
    if code != 0:
        raise BackupError(f"The media sync failed (rclone exit {code}); see the log.")

    _, out = _capture(
        rclone("size", "--json", "--fast-list", remote_path(MEDIA_CURRENT)),
        what="Measuring the media copy", timeout=1800,
    )
    size = json.loads(out)
    run.media_files, run.media_bytes = size.get("count"), size.get("bytes")
    rec.log(
        f"Media synced: {run.media_uploaded or 0} files uploaded, {run.media_archived or 0} moved "
        f"to the archive; the copy holds {run.media_files} files, {human_size(run.media_bytes)}."
    )


def list_remote_snapshots():
    """
    {snapshot_id: {"file", "bytes", "complete", "files"}} for remote db/.
    A snapshot is complete once its .sha256 exists (uploaded after the dump).
    """
    snapshots = {}
    for line in _lsf(remote_path(DB_DIR), "--files-only", "--format", "ps", "--separator", "\t"):
        name, _, size = line.rpartition("\t")
        if name.endswith(".dump.sha256"):
            match = DUMP_RE.match(name[: -len(".sha256")])
            if match:
                entry = snapshots.setdefault(match["id"], {"file": None, "bytes": None, "complete": False, "files": []})
                entry["complete"] = True
                entry["files"].append(name)
            continue
        match = DUMP_RE.match(name)
        if match:
            entry = snapshots.setdefault(match["id"], {"file": None, "bytes": None, "complete": False, "files": []})
            entry["file"] = name
            entry["bytes"] = int(size) if size.isdigit() else None
            entry["files"].append(name)
    for entry in snapshots.values():
        entry["complete"] = entry["complete"] and entry["file"] is not None
    return snapshots


def _delete_remote_files(rec, folder, names):
    if not names:
        return
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as listing:
        listing.write("\n".join(names) + "\n")
    try:
        # Straight to deleted, not to the Drive trash, where pruned backups
        # would go on counting against the storage quota for 30 days.
        _capture(
            rclone("delete", folder, "--files-from-raw", listing.name, "--drive-use-trash=false"),
            what=f"Pruning {folder}", timeout=900,
        )
    finally:
        os.unlink(listing.name)
    rec.log(f"Removed {len(names)} old files from {folder}: {', '.join(sorted(names))}")


def prune_remote(rec, current):
    """Apply the retention policy off the server; return the snapshot list kept."""
    snapshots = list_remote_snapshots()
    complete = [snapshot_id for snapshot_id, entry in snapshots.items() if entry["complete"]]
    keep, monthly = plan_retention(complete, settings.BACKUP_KEEP_DAILY, settings.BACKUP_KEEP_MONTHLY)
    keep.add(current)

    doomed = []
    for snapshot_id, entry in snapshots.items():
        # Unfinished uploads (no checksum) from earlier runs go too.
        if snapshot_id not in keep or not entry["complete"]:
            if snapshot_id != current:
                doomed.extend(entry["files"])
    _delete_remote_files(rec, remote_path(DB_DIR), doomed)

    env_ids = {}
    for name in _lsf(remote_path(ENV_DIR), "--files-only"):
        match = ENV_RE.match(name)
        if match:
            env_ids[match["id"]] = name
    _delete_remote_files(
        rec, remote_path(ENV_DIR),
        [name for snapshot_id, name in env_ids.items() if snapshot_id not in keep],
    )

    cutoff = timezone.now() - timedelta(days=settings.BACKUP_MEDIA_ARCHIVE_DAYS)
    for folder in _lsf(remote_path(MEDIA_ARCHIVE), "--dirs-only"):
        snapshot_id = folder.rstrip("/")
        when = snapshot_time(snapshot_id)
        if when is not None and when < cutoff and snapshot_id != current:
            _capture(
                rclone("purge", remote_path(MEDIA_ARCHIVE, snapshot_id), "--drive-use-trash=false"),
                what=f"Pruning media archive {snapshot_id}", timeout=1800,
            )
            rec.log(f"Removed media archive {snapshot_id} (older than {settings.BACKUP_MEDIA_ARCHIVE_DAYS} days).")

    daily = set(sorted(complete, reverse=True)[: max(settings.BACKUP_KEEP_DAILY, 0)]) | {current}
    kept = [
        {
            "id": snapshot_id,
            "file": snapshots[snapshot_id]["file"],
            "bytes": snapshots[snapshot_id]["bytes"],
            # Kept only by the monthly rule: older than the daily window.
            "monthly": snapshot_id in monthly and snapshot_id not in daily,
            "env": snapshot_id in env_ids,
        }
        for snapshot_id in sorted(keep, reverse=True)
        if snapshot_id in snapshots and snapshots[snapshot_id]["complete"]
    ]
    rec.log(f"Retention: {len(kept)} snapshot{'' if len(kept) == 1 else 's'} kept off the server.")
    return kept


def backup(rec, run, deadline=None, **_):
    checks = Checks(rec)
    check_local(checks)
    run.details["checks"] = list(checks)
    checks.require()

    run.snapshot = new_snapshot_id()
    _save(run, "snapshot", "details")
    dump = dump_database(rec, run.snapshot)
    run.db_bytes = dump.stat().st_size
    _save(run, "db_bytes")
    prune_local(rec, dump)

    info = check_remote(checks)
    env_file = check_env_file(checks, info["encrypted"]) if info["type"] else None
    check_media(checks)
    run.details.update(checks=list(checks), remote=_public_info(info))
    _save(run, "details")
    try:
        checks.require()
    except BackupError as exc:
        raise BackupError(
            f"The database was dumped on the server ({dump.name}), but nothing was copied off it. {exc}"
        ) from exc

    upload_dump(rec, dump)
    if env_file:
        upload_env(rec, run.snapshot, env_file)
    sync_media(rec, run, run.snapshot, deadline)
    run.details["snapshots"] = prune_remote(rec, run.snapshot)
    try:
        _, out = _capture(rclone("about", "--json", remote_path()), what="rclone about", timeout=180)
        run.details["remote"]["usage"] = json.loads(out)
    except (BackupError, ValueError):
        pass

    # The rows only feed the dashboard; a year of them is plenty.
    BackupRun.objects.filter(created_at__lt=timezone.now() - timedelta(days=365)).delete()


# ── Verifying ─────────────────────────────────────────────────────────────


def _last_backup_start():
    return (
        BackupRun.objects.filter(kind=BackupRun.Kind.BACKUP, status=BackupRun.Status.SUCCEEDED)
        .order_by("-started_at").values_list("started_at", flat=True).first()
    )


def verify_media(rec):
    """
    Compare MEDIA_ROOT with the off-server copy by name and size. A file
    missing off the server is a fault only if it is older than the last
    backup; newer files are simply waiting for the next one.
    """
    root = Path(settings.MEDIA_ROOT)
    with tempfile.TemporaryDirectory() as scratch:
        missing_list = Path(scratch) / "missing.txt"
        differ_list = Path(scratch) / "differ.txt"
        rec.log("Comparing media with the off-server copy …")
        _capture(
            rclone(
                "check", root, remote_path(MEDIA_CURRENT), "--one-way", "--size-only", "--fast-list",
                "--missing-on-dst", missing_list, "--differ", differ_list, *_exclude_flags(),
            ),
            what="rclone check", timeout=3600, ok_codes=(0, 1),
        )
        suspects = []
        for listing in (missing_list, differ_list):
            if listing.exists():
                suspects += [line for line in listing.read_text().splitlines() if line]

    since = _last_backup_start()
    stale, recent = [], 0
    for relative in suspects:
        try:
            modified = (root / relative).stat().st_mtime
        except OSError:
            continue  # deleted since the check listed it
        if since is not None and modified < since.timestamp() - 120:
            stale.append(relative)
        else:
            recent += 1
    rec.log(f"Media: {recent} files are newer than the last backup (the next one uploads them).")
    if stale:
        sample = ", ".join(stale[:10])
        raise BackupError(
            f"{len(stale)} media files that existed before the last backup are missing or different "
            f"off the server, e.g. {sample}. Run a backup and check its log for upload errors."
        )
    rec.log("✓ Every media file older than the last backup is in the off-server copy.")
    return {"newer_than_backup": recent}


def verify(rec, run, snapshot=None, **_):
    """
    Prove a snapshot restores: download it from the remote, match its
    checksum, and have pg_restore read every byte of it back.
    """
    checks = Checks(rec)
    check_local(checks)
    info = check_remote(checks)
    run.details.update(checks=list(checks), remote=_public_info(info))
    _save(run, "details")
    checks.require()

    snapshots = list_remote_snapshots()
    complete = sorted((sid for sid, entry in snapshots.items() if entry["complete"]), reverse=True)
    if not complete:
        raise BackupError("There is no complete snapshot off the server yet. Run a backup first.")
    snapshot = snapshot or complete[0]
    if snapshot not in complete:
        raise BackupError(f"No complete snapshot {snapshot} off the server (newest: {complete[0]}).")
    entry = snapshots[snapshot]
    run.snapshot = snapshot
    _save(run, "snapshot")

    folder = local_dir("verify")
    for leftover in folder.iterdir():
        leftover.unlink(missing_ok=True)
    _require_space(folder)
    local = folder / entry["file"]
    try:
        rec.log(f"Downloading {entry['file']} …")
        code = _rclone_streamed(
            rec, "copyto", remote_path(DB_DIR, entry["file"]), local,
            formatter=RcloneProgress("downloaded"),
        )
        if code != 0:
            raise BackupError(f"Downloading {entry['file']} failed (rclone exit {code}).")
        _, out = _capture(
            rclone("cat", remote_path(DB_DIR, entry["file"] + ".sha256")), what="Reading the checksum",
        )
        expected = out.split()[0] if out.split() else ""
        actual = sha256_file(local)
        if actual != expected:
            raise BackupError(
                f"{entry['file']} is damaged: its checksum is {actual}, the backup recorded {expected}."
            )
        rec.log("✓ The checksum matches the one recorded at backup time.")
        entries = _dump_entries(local)
        code = _run_streamed(rec, ["pg_restore", "--file=/dev/null", local])
        if code != 0:
            raise BackupError("pg_restore could not read the whole dump back; see the log.")
        rec.log(f"✓ pg_restore read all {entries} objects and every table's data.")
        run.db_bytes = local.stat().st_size
    finally:
        local.unlink(missing_ok=True)

    findings = {"snapshot": snapshot, "objects": entries}
    if info["encrypted"] and settings.BACKUP_ENV_FILE:
        has_env = f"{snapshot}.env" in _lsf(remote_path(ENV_DIR), "--files-only")
        findings["env"] = has_env
        rec.log("✓ The snapshot includes the .env." if has_env else "! The snapshot has no copy of the .env.")
    findings["media"] = verify_media(rec)
    run.details["verify"] = findings


def check_setup(rec, run, **_):
    """Everything a backup needs, without making one."""
    checks = Checks(rec)
    check_local(checks)
    info = check_remote(checks)
    if info["type"]:
        check_env_file(checks, info["encrypted"])
    check_media(checks)
    run.details.update(checks=list(checks), remote=_public_info(info))
    checks.require()


# ── Running ───────────────────────────────────────────────────────────────


def _public_info(info):
    return {key: info.get(key) for key in ("remote", "type", "encrypted", "usage")}


def _save(run, *fields):
    # Never `log`: the Recorder owns that column while the run is going.
    run.save(update_fields=list(fields))


@contextmanager
def exclusive_lock():
    """One run at a time per server; the lock goes when the process does."""
    try:
        handle = open(local_dir() / ".lock", "a")
    except OSError as exc:
        raise BackupError(
            f"{settings.BACKUP_LOCAL_DIR} is not writable by uid {os.getuid()} ({exc.strerror or exc})."
        ) from exc
    with handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackupError("Another backup, verification or check is running. Wait for it to finish.") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


STEPS = {
    BackupRun.Kind.BACKUP: backup,
    BackupRun.Kind.VERIFY: verify,
    BackupRun.Kind.CHECK: check_setup,
}


def perform(run, *, deadline=None, echo=None, snapshot=None):
    """
    Carry out `run` and record the outcome on it. Raises on failure (after
    saving it), so Django-Q lists the task as failed too.

    `deadline` is a time.monotonic() value media uploads must stop by.
    """
    rec = Recorder(run, echo=echo)
    run.status = BackupRun.Status.RUNNING
    run.started_at = timezone.now()
    run.error = ""
    _save(run, "status", "started_at", "error")
    rec.log(f"{run.get_kind_display()} started ({run.get_trigger_display().lower()}).")
    try:
        with exclusive_lock():
            # Holding the lock proves no other run is alive: any still marked
            # running died with its worker (a restart or the task timeout).
            BackupRun.objects.filter(status=BackupRun.Status.RUNNING).exclude(pk=run.pk).update(
                status=BackupRun.Status.FAILED, finished_at=timezone.now(),
                error="Interrupted: the worker stopped before this run finished.",
            )
            STEPS[run.kind](rec, run, deadline=deadline, snapshot=snapshot)
    except BackupError as exc:
        run.status = BackupRun.Status.FAILED
        run.error = str(exc)
        rec.log(f"✗ {exc}")
        raise
    except Exception as exc:
        run.status = BackupRun.Status.FAILED
        run.error = f"Unexpected error: {exc.__class__.__name__}: {exc}"
        rec.log(traceback.format_exc())
        raise
    else:
        run.status = BackupRun.Status.SUCCEEDED
        rec.log(f"✓ {run.get_kind_display()} finished.")
    finally:
        run.finished_at = timezone.now()
        run.log = rec.text()
        run.save()
