"""Download / refresh the offline GeoIP databases used by the analytics page.

Fetches two free, no-API-key **DB-IP Lite** builds (CC BY 4.0):
  * City -> <GEOIP_PATH>/<GEOIP_DB_FILENAME>      (country + city)
  * ASN  -> <GEOIP_PATH>/<GEOIP_ASN_DB_FILENAME>  (network / datacenter detection)

By default it fetches the current month's builds. Re-run monthly to keep them
fresh (the qcluster schedule does this automatically)::

    python manage.py update_geoip
    python manage.py update_geoip --month 2026-07
    python manage.py update_geoip --only asn --force

Attribution: IP Geolocation by DB-IP (https://db-ip.com).
"""
import gzip
import shutil
import tempfile
import time
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

DB_IP_CITY_URL = "https://download.db-ip.com/free/dbip-city-lite-{ym}.mmdb.gz"
DB_IP_ASN_URL = "https://download.db-ip.com/free/dbip-asn-lite-{ym}.mmdb.gz"


class Command(BaseCommand):
    help = "Download or refresh the offline DB-IP Lite GeoIP (city + ASN) databases."

    def add_arguments(self, parser):
        parser.add_argument(
            "--url", help="Override the source URL of the City .mmdb(.gz).",
        )
        parser.add_argument(
            "--asn-url", help="Override the source URL of the ASN .mmdb(.gz).",
        )
        parser.add_argument(
            "--month",
            help="YYYY-MM of the DB-IP Lite build to fetch (default: current month).",
        )
        parser.add_argument(
            "--only", choices=["city", "asn"],
            help="Fetch only one of the two databases.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Download even if a recent database file already exists.",
        )
        parser.add_argument(
            "--max-age-days",
            type=int,
            default=25,
            help="Skip the download if the existing DB is younger than this many "
                 "days (default: 25). Ignored with --force.",
        )

    def handle(self, *args, **options):
        base = getattr(settings, "GEOIP_PATH", None)
        if not base:
            raise CommandError("GEOIP_PATH is not configured in settings.")
        dest_dir = Path(base)
        dest_dir.mkdir(parents=True, exist_ok=True)

        ym = options.get("month") or date.today().strftime("%Y-%m")
        city_name = getattr(settings, "GEOIP_DB_FILENAME", "dbip-city-lite.mmdb")
        asn_name = getattr(settings, "GEOIP_ASN_DB_FILENAME", "dbip-asn-lite.mmdb")

        only = options.get("only")
        specs = []
        if only != "asn":
            specs.append(("City", city_name, options.get("url") or DB_IP_CITY_URL.format(ym=ym)))
        if only != "city":
            specs.append(("ASN", asn_name, options.get("asn_url") or DB_IP_ASN_URL.format(ym=ym)))

        downloaded = 0
        for label, filename, url in specs:
            if self._fetch_one(label, dest_dir, filename, url, options):
                downloaded += 1

        if downloaded:
            # Drop cached readers so the fresh files are picked up in-process.
            try:
                from home.geoip_utils import reset_cache
                reset_cache()
            except Exception:
                pass

        self.stdout.write(
            "Attribution: IP Geolocation by DB-IP (https://db-ip.com) — CC BY 4.0."
        )

    def _fetch_one(self, label, dest_dir, filename, url, options):
        """Download one DB into ``dest_dir/filename``. Returns True if downloaded."""
        import requests  # already a project dependency

        dest = dest_dir / filename

        # Idempotent: skip when a recent copy already exists, so the boot-time
        # seed in the Dockerfile is a cheap no-op on every restart. The monthly
        # scheduled refresh (home.tasks.refresh_geoip_db) passes --force.
        if not options.get("force") and dest.exists():
            age_days = (time.time() - dest.stat().st_mtime) / 86400
            max_age = options.get("max_age_days") or 25
            if age_days < max_age:
                self.stdout.write(
                    f"{label} DB at {dest} is {age_days:.1f} day(s) old "
                    f"(< {max_age}); skipping. Use --force to override."
                )
                return False

        self.stdout.write(f"Downloading {label} database from {url} …")
        try:
            resp = requests.get(url, stream=True, timeout=120)
            resp.raise_for_status()
        except Exception as e:
            raise CommandError(f"{label} download failed: {e}")

        gzipped = url.endswith(".gz")
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                delete=False, dir=dest_dir, suffix=".tmp"
            ) as tmp:
                tmp_path = Path(tmp.name)
                for chunk in resp.iter_content(chunk_size=1 << 16):
                    if chunk:
                        tmp.write(chunk)

            if gzipped:
                decompressed = dest_dir / (filename + ".raw")
                with gzip.open(tmp_path, "rb") as fin, open(decompressed, "wb") as fout:
                    shutil.copyfileobj(fin, fout)
                tmp_path.unlink(missing_ok=True)
                tmp_path = None
                decompressed.replace(dest)
            else:
                tmp_path.replace(dest)
                tmp_path = None
        except Exception as e:
            raise CommandError(f"Failed to write {label} database: {e}")
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

        size_mb = dest.stat().st_size / (1024 * 1024)
        self.stdout.write(self.style.SUCCESS(
            f"{label} database saved to {dest} ({size_mb:.1f} MB)."
        ))
        return True
