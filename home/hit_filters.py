"""
Single source of truth for "should this page view be counted as a real read?"

``django-hitcount`` records a Hit on every non-staff page view. That over-counts
on a pre-launch site: scrapers forging a normal browser User-Agent are recorded
as genuine reads, inflating homepage hits and "most read". A UA string alone
can't catch them (it's trivially spoofed), so this gate adds the one strong
offline signal — the client IP's network type. Real readers browse from
residential / mobile ISPs; scanners come from cloud / hosting networks.

``is_countable_human(request)`` returns False when the visitor is staff, a known
bot by UA (``home.ua_utils``), or on a datacenter IP (``home.geoip_utils``). It
degrades safely: if the ASN database isn't installed, ``is_datacenter_ip``
returns False and counting behaves exactly as before.
"""
from home import ua_utils, geoip_utils


def client_ip(request):
    """Best-effort client IP, mirroring ``reader.adapter.get_client_ip``
    (first X-Forwarded-For hop, else REMOTE_ADDR) so it matches the IP
    django-hitcount records."""
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def is_countable_human(request):
    """True only for a page view that should increment the hit count.

    False for staff/superusers, any User-Agent that classifies as a bot (search,
    AI, scraper, social, feed) or is empty/unknown, and any client on a
    datacenter / hosting network. Never raises.
    """
    user = getattr(request, "user", None)
    if user is not None and (getattr(user, "is_superuser", False)
                             or getattr(user, "is_staff", False)):
        return False

    ua = request.META.get("HTTP_USER_AGENT", "")
    if ua_utils.classify_user_agent(ua)["category"] != ua_utils.CATEGORY_HUMAN:
        return False

    try:
        if geoip_utils.is_datacenter_ip(client_ip(request)):
            return False
    except Exception:
        # Never let bot-detection break page counting / serving.
        pass

    return True
