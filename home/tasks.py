"""Background tasks for the ``home`` app, executed by the django-Q2 qcluster."""
from django.core.management import call_command


def refresh_geoip_db():
    """Refresh the offline GeoIP database used by the analytics dashboard.

    Registered as a monthly Django-Q2 schedule by
    ``manage.py setup_geoip_schedule`` and run by the qcluster worker. Forces a
    download so each month's new DB-IP City Lite build is pulled even if the
    current file is not yet past the update_geoip freshness threshold.
    """
    call_command("update_geoip", force=True)
