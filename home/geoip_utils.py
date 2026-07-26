"""
Offline IP geolocation for the analytics dashboard.

``django-hitcount`` stores the client ``ip`` on every ``Hit`` row, and
``reader.ReaderUser`` stores ``registration_ip`` / ``last_login_ip``, but
nothing in the project ever turns those into a country or city. This helper
resolves an IP to a coarse location using a *local* MaxMind-format database
(read via the ``geoip2`` package), so no visitor IP is ever sent to a
third-party service — matching the project's privacy posture.

The database file is intentionally *not* committed to the repo. Point
``GEOIP_PATH`` at a directory containing ``GEOIP_DB_FILENAME`` (defaults to the
free, no-API-key DB-IP City Lite build); ``python manage.py update_geoip``
downloads / refreshes it. Every entry point degrades gracefully — a missing
``geoip2`` package, a missing DB file, or a private/invalid IP all return an
empty ("Unknown") result rather than raising, so the dashboard keeps rendering.
"""
import ipaddress
import logging
import threading
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

UNKNOWN = "Unknown"

_reader = None
_reader_tried = False
_asn_reader = None
_asn_reader_tried = False
_lock = threading.Lock()

# Substrings (lowercase) of ASN organisation names that indicate a cloud /
# hosting / datacenter network rather than a residential or mobile ISP. A
# browser-UA hit from one of these is almost certainly an automated scraper, not
# a reader — real humans don't browse from AWS. Extendable; matched with a plain
# substring test so e.g. "amazon" catches "AMAZON-02", "Amazon Data Services".
_DATACENTER_ASN_KEYWORDS = (
    "amazon", "aws", "google", "gcp", "microsoft", "azure", "oracle cloud",
    "digitalocean", "linode", "akamai", "cloudflare", "fastly", "ovh",
    "hetzner", "contabo", "leaseweb", "scaleway", "vultr", "choopa", "m247",
    "datacamp", "hostinger", "namecheap", "godaddy", "ionos", "1&1",
    "alibaba", "aliyun", "tencent", "huawei cloud", "ucloud", "kingsoft",
    "baidu", "china mobile communications", "chinanet backbone",
    "digital ocean", "upcloud", "hostwinds", "psychz", "quadranet",
    "colocrossing", "servermania", "ovhcloud", "netcup", "time4vps",
    "webnx", "hivelocity", "limestone", "reliablesite", "phoenixnap",
    "datawagon", "gcore", "stackpath", "bunny", "cdn", "hosting", "datacenter",
    "data center", "server", "cloud", "vps", "dedicated",
)


def _db_path():
    base = getattr(settings, "GEOIP_PATH", None)
    if not base:
        return None
    filename = getattr(settings, "GEOIP_DB_FILENAME", "dbip-city-lite.mmdb")
    return Path(base) / filename


def _asn_db_path():
    base = getattr(settings, "GEOIP_PATH", None)
    if not base:
        return None
    filename = getattr(settings, "GEOIP_ASN_DB_FILENAME", "dbip-asn-lite.mmdb")
    return Path(base) / filename


def _get_reader():
    """Return a cached ``geoip2`` Reader, or ``None`` if unavailable.

    Thread-safe and memoised: the DB file is opened at most once per process,
    and a failed attempt is remembered so we don't re-probe on every request.
    """
    global _reader, _reader_tried
    if _reader is not None:
        return _reader
    if _reader_tried:
        return None
    with _lock:
        if _reader is not None:
            return _reader
        if _reader_tried:
            return None
        _reader_tried = True
        path = _db_path()
        if not path or not path.exists():
            return None
        try:
            import geoip2.database
            _reader = geoip2.database.Reader(str(path))
        except Exception as e:  # ImportError, corrupt/incompatible DB, etc.
            logger.warning("GeoIP database unavailable: %s", e)
            _reader = None
        return _reader


def geoip_available():
    """True when a usable GeoIP (city) database is loaded."""
    return _get_reader() is not None


def _get_asn_reader():
    """Return a cached ``geoip2`` ASN Reader, or ``None`` if unavailable.

    Same memoised, thread-safe, degrade-to-None contract as :func:`_get_reader`,
    but for the separate ASN database (a City DB cannot answer ``.asn()``).
    """
    global _asn_reader, _asn_reader_tried
    if _asn_reader is not None:
        return _asn_reader
    if _asn_reader_tried:
        return None
    with _lock:
        if _asn_reader is not None:
            return _asn_reader
        if _asn_reader_tried:
            return None
        _asn_reader_tried = True
        path = _asn_db_path()
        if not path or not path.exists():
            return None
        try:
            import geoip2.database
            _asn_reader = geoip2.database.Reader(str(path))
        except Exception as e:
            logger.warning("GeoIP ASN database unavailable: %s", e)
            _asn_reader = None
        return _asn_reader


def asn_available():
    """True when a usable ASN database is loaded."""
    return _get_asn_reader() is not None


def _is_public(ip):
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (
        addr.is_private or addr.is_loopback or addr.is_reserved
        or addr.is_link_local or addr.is_multicast or addr.is_unspecified
    )


def lookup_ip(ip):
    """Resolve an IP to ``{'country', 'country_code', 'city'}``.

    ``country`` / ``city`` fall back to :data:`UNKNOWN` / ``''`` when the IP
    can't be resolved (no DB, private/invalid IP, or not found). Never raises.
    """
    result = {"country": UNKNOWN, "country_code": "", "city": ""}
    if not ip or not _is_public(str(ip)):
        return result
    reader = _get_reader()
    if reader is None:
        return result

    try:
        resp = reader.city(str(ip))
    except (ValueError, TypeError):
        # The configured DB may be a Country-only build; fall back to it.
        try:
            resp = reader.country(str(ip))
        except Exception:
            return result
    except Exception:
        # AddressNotFoundError and friends — leave the Unknown default.
        return result

    country = getattr(resp, "country", None)
    if country is not None and country.name:
        result["country"] = country.name
        result["country_code"] = country.iso_code or ""
    city = getattr(resp, "city", None)
    if city is not None and getattr(city, "name", None):
        result["city"] = city.name
    return result


def lookup_asn(ip):
    """Resolve an IP to ``{'asn', 'org'}`` (AS number + organisation name).

    ``asn`` is 0 and ``org`` is ``''`` when the IP can't be resolved (no ASN DB,
    private/invalid IP, or not found). Never raises.
    """
    result = {"asn": 0, "org": ""}
    if not ip or not _is_public(str(ip)):
        return result
    reader = _get_asn_reader()
    if reader is None:
        return result
    try:
        resp = reader.asn(str(ip))
    except Exception:
        return result
    result["asn"] = getattr(resp, "autonomous_system_number", 0) or 0
    result["org"] = getattr(resp, "autonomous_system_organization", "") or ""
    return result


def is_datacenter_org(org):
    """True when an ASN organisation name looks like a hosting / datacenter
    network (see :data:`_DATACENTER_ASN_KEYWORDS`). Empty/None -> False."""
    org = (org or "").lower()
    if not org:
        return False
    return any(keyword in org for keyword in _DATACENTER_ASN_KEYWORDS)


def is_datacenter_ip(ip):
    """True when the IP belongs to a cloud / hosting / datacenter network.

    A browser-UA hit from such a network is treated as a suspected bot. Returns
    ``False`` when the ASN DB is absent, so behaviour is unchanged until the DB
    is installed (``python manage.py update_geoip``).
    """
    return is_datacenter_org(lookup_asn(ip)["org"])


def reset_cache():
    """Drop the cached readers (used by ``update_geoip`` after a refresh)."""
    global _reader, _reader_tried, _asn_reader, _asn_reader_tried
    with _lock:
        for r in (_reader, _asn_reader):
            if r is not None:
                try:
                    r.close()
                except Exception:
                    pass
        _reader = None
        _reader_tried = False
        _asn_reader = None
        _asn_reader_tried = False
