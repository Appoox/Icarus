"""Background tasks for the ``home`` app, executed by the django-Q2 qcluster."""
import shutil

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import mail_admins
from django.core.management import call_command

# Email ADMINS when the host disk is at least this full, measured like df's
# "Use%" (see check_disk_usage).
DISK_ALERT_THRESHOLD = 0.80

# The path whose filesystem is measured. In the qcluster container "/" is an
# overlay that reports the filesystem holding Docker's data (images, volumes,
# container logs): the host root disk on icarus-flight. The 2026-10-22
# incident's df showed the overlays at the same 38 GB / 37 GB as /dev/sda1.
DISK_ALERT_PATH = "/"


def refresh_geoip_db():
    """Refresh the offline GeoIP database used by the analytics dashboard.

    Registered as a monthly Django-Q2 schedule by
    ``manage.py setup_geoip_schedule`` and run by the qcluster worker. Forces a
    download so each month's new DB-IP City Lite build is pulled even if the
    current file is not yet past the update_geoip freshness threshold.
    """
    call_command("update_geoip", force=True)


def check_disk_usage():
    """Email ADMINS when the host disk is fuller than DISK_ALERT_THRESHOLD.

    Registered as an hourly Django-Q2 schedule by
    ``manage.py setup_disk_alert_schedule`` and run by the qcluster worker.
    Added after the 2026-10-22 incident, when the disk sat full for days
    before anyone noticed.

    Sends on every run while above the threshold, so it keeps nagging until
    someone acts. Fails loudly rather than sending nothing: with ADMINS empty
    it raises (mail_admins() alone would skip silently), and a mail error
    propagates (fail_silently defaults to False). Either one shows up as a
    failed task in the Django-Q admin and in the qcluster log.

    Returns the measured fraction, which Django-Q stores as the task result.
    """
    if not settings.ADMINS:
        raise ImproperlyConfigured(
            "ADMINS is empty, so the disk-usage alert has nobody to email. "
            "Set ADMIN_EMAILS in the server's .env."
        )

    usage = shutil.disk_usage(DISK_ALERT_PATH)
    # The same figure as df's Use%: used / (used + available). usage.total
    # also counts the blocks ext4 reserves for root (5% by default), so
    # used / total would read about 4 points low at the threshold.
    used_fraction = usage.used / (usage.used + usage.free)

    if used_fraction >= DISK_ALERT_THRESHOLD:
        # Admin-only and English, with no gettext: the one deliberate
        # exception to Malayalam msgids, since no reader ever sees it.
        mail_admins(
            subject=f"Disk at {used_fraction:.0%} on icarus-flight",
            message=(
                f"{usage.used / 1e9:.1f} GB used of {usage.total / 1e9:.1f} GB, "
                f"{usage.free / 1e9:.1f} GB free "
                f"(alert threshold {DISK_ALERT_THRESHOLD:.0%}).\n\n"
                "On the server, start with `df -h /` and `docker system df`,\n"
                "then `sudo du -xh -d1 /var/lib/docker | sort -h` to see what grew.\n"
            ),
        )
    return used_fraction
