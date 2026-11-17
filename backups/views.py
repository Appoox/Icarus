"""
The backups dashboard in the Wagtail admin, at /admin/backups/.

It starts runs and shows what they did; it never does the work itself. A
click queues a Django-Q task, and the qcluster worker (which has rclone, its
config and pg_dump) carries it out while this page polls the run's log.
Everything shown comes from BackupRun rows and Django-Q's schedules: the web
container has no rclone config and never talks to Google Drive.

There is deliberately no restore button. Restoring replaces the live
database, which the app cannot safely do to itself while it is running on
it, and a stolen admin session must not be able to roll the site back.
Restores run on the server with restore.sh; this page shows the commands.
"""
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django_q.models import Schedule
from django_q.tasks import async_task
from wagtail.admin import messages

from backups import engine
from backups.health import backup_health
from backups.management.commands.setup_backup_schedules import schedules
from backups.models import BackupRun, snapshot_time
from backups.permissions import can_manage_backups

# Times on these pages are shown in IST, like the audit log.
DISPLAY_TIME_ZONE = "Asia/Kolkata"

# A queued run the worker hasn't picked up by now suggests qcluster is down.
QUEUED_TOO_LONG = timedelta(minutes=5)

STARTABLE = {
    "backup": BackupRun.Kind.BACKUP,
    "verify": BackupRun.Kind.VERIFY,
    "check": BackupRun.Kind.CHECK,
}

LOG_LIMIT = 60_000


def _require_access(request):
    if not can_manage_backups(request.user):
        raise PermissionDenied


def _active_run():
    return BackupRun.objects.filter(status__in=BackupRun.ACTIVE_STATUSES).order_by("created_at").first()


def _latest_with(key):
    """The newest finished run whose details carry `key`."""
    return (
        BackupRun.objects.exclude(status__in=BackupRun.ACTIVE_STATUSES)
        .filter(details__has_key=key)
        .order_by("-created_at")
        .first()
    )


def _snapshots(run):
    rows = []
    for entry in (run.details.get("snapshots") or []) if run else []:
        rows.append({**entry, "time": snapshot_time(entry.get("id"))})
    return rows


def _usage():
    """Remote storage as last reported: None before any run, {} if the remote doesn't say."""
    run = _latest_with("remote")
    if run is None:
        return None
    usage = (run.details.get("remote") or {}).get("usage") or {}
    if not any(usage.get(key) is not None for key in ("used", "free", "total")):
        return {}
    total, used = usage.get("total"), usage.get("used")
    return {
        **usage,
        "percent": round(100 * used / total) if total and used is not None else None,
        "as_of": run.finished_at,
    }


def dashboard(request):
    _require_access(request)
    health = backup_health()
    active = _active_run()
    checks_run = _latest_with("checks")
    names = [name for name, _, _ in schedules()]
    schedule_rows = {row.name: row for row in Schedule.objects.filter(name__in=names)}

    if health["problems"]:
        state = "bad"
    elif health["warnings"]:
        state = "warn"
    else:
        state = "ok"

    return render(request, "backups/dashboard.html", {
        "display_tz": DISPLAY_TIME_ZONE,
        "health": health,
        "state": state,
        "active": active,
        "active_stuck": bool(
            active and active.status == BackupRun.Status.QUEUED
            and timezone.now() - active.created_at > QUEUED_TOO_LONG
        ),
        "runs": BackupRun.objects.select_related("requested_by")[:25],
        "checks": (checks_run.details.get("checks") or []) if checks_run else [],
        "checks_run": checks_run,
        "snapshots": _snapshots(health["last_ok"]),
        "usage": _usage(),
        "next_backup": schedule_rows.get(names[0]),
        "next_verify": schedule_rows.get(names[1]),
        "alert_schedule": schedule_rows.get(names[2]),
        "remote": settings.BACKUP_REMOTE,
        "max_age_hours": settings.BACKUP_MAX_AGE_HOURS,
        "keep_daily": settings.BACKUP_KEEP_DAILY,
        "keep_monthly": settings.BACKUP_KEEP_MONTHLY,
        "archive_days": settings.BACKUP_MEDIA_ARCHIVE_DAYS,
        "status_url_base": reverse("backups_status"),
    })


@require_POST
def start(request, kind):
    _require_access(request)
    if kind not in STARTABLE:
        raise Http404
    if _active_run() is not None:
        messages.warning(request, "A run is already queued or running. Wait for it to finish first.")
        return redirect("backups_dashboard")

    snapshot = (request.POST.get("snapshot") or "").strip() or None
    if snapshot and (kind != "verify" or not engine.SNAPSHOT_RE.match(snapshot)):
        messages.error(request, "That is not a snapshot ID.")
        return redirect("backups_dashboard")

    run = BackupRun.objects.create(
        kind=STARTABLE[kind], trigger=BackupRun.Trigger.MANUAL, requested_by=request.user,
        snapshot=snapshot or "",
    )
    try:
        async_task("backups.tasks.perform_run", run.pk, snapshot, task_name=f"backups-{kind}-{run.pk}")
    except Exception as exc:  # the queue is a database table; this is rare
        run.status = BackupRun.Status.FAILED
        run.error = f"Could not queue the run: {exc}"
        run.finished_at = timezone.now()
        run.save()
        messages.error(request, run.error)
        return redirect("backups_dashboard")

    messages.success(
        request,
        f"{run.get_kind_display()} #{run.pk} queued. The background worker starts it within seconds; "
        "you can leave this page, it carries on.",
    )
    return redirect("backups_dashboard")


def run_detail(request, pk):
    _require_access(request)
    run = get_object_or_404(BackupRun.objects.select_related("requested_by"), pk=pk)
    return render(request, "backups/run_detail.html", {
        "display_tz": DISPLAY_TIME_ZONE,
        "header_title": run.get_kind_display(),
        "header_subtitle": f"Run #{run.pk}",
        "run": run,
        "checks": run.details.get("checks") or [],
        "verify": run.details.get("verify") or {},
        "snapshots": _snapshots(run),
        "status_url_base": reverse("backups_status"),
    })


@require_GET
def status(request):
    """A run's progress, for the dashboard's live log. ?run=<id>"""
    _require_access(request)
    run_id = request.GET.get("run", "")
    if not run_id.isdigit():
        raise Http404
    run = get_object_or_404(BackupRun, pk=int(run_id))
    return JsonResponse({
        "id": run.pk,
        "status": run.status,
        "status_label": run.get_status_display(),
        "active": run.is_active,
        "log": run.log[-LOG_LIMIT:],
        "error": run.error,
    })
