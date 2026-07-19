"""
Verified-crawler detection for flexible-sampling paywall bypass.

Serves full article bodies to *verified* search crawlers (Googlebot, Bingbot)
so paywalled content stays fully indexable even when ``FREE_ARTICLE_LIMIT`` is
0. A User-Agent string is trivially spoofed, so a claimed crawler is confirmed
with Google's documented reverse-then-forward DNS method against a *trusted*
client IP — the Caddy-set ``X-Real-IP`` header, never the client-supplied
``X-Forwarded-For`` (which ``reader.adapter.get_client_ip`` reads and which an
attacker could set to a real Googlebot address to unlock the paywall).
"""
import socket
import threading
import time

from django.conf import settings

# UA substring (lowercase) -> allowed reverse-DNS hostname suffixes.
_CRAWLER_RDNS_SUFFIXES = {
    "googlebot": (".googlebot.com", ".google.com"),
    "google-inspectiontool": (".googlebot.com", ".google.com"),
    "storebot-google": (".googlebot.com", ".google.com"),
    "bingbot": (".search.msn.com",),
    "adidxbot": (".search.msn.com",),
}

# Per-process TTL cache: ip -> (is_verified: bool, expires_at: float).
# LocMem is the default (per-process) cache anyway, so a plain dict keeps this
# self-contained; DNS lookups are cheap and only run for crawler-UA requests.
_CACHE_MAX = 10000
_verify_cache = {}
_cache_lock = threading.Lock()


def _matched_suffixes(user_agent):
    """Allowed rDNS suffixes for the first crawler token found in the UA, else None."""
    ua = (user_agent or "").lower()
    for token, suffixes in _CRAWLER_RDNS_SUFFIXES.items():
        if token in ua:
            return suffixes
    return None


def _trusted_client_ip(request):
    # Caddy overwrites X-Real-IP with the true peer, so the client cannot forge
    # it. REMOTE_ADDR is the fallback for non-proxied / test setups.
    header = getattr(settings, "CRAWLER_TRUSTED_IP_HEADER", "HTTP_X_REAL_IP")
    ip = request.META.get(header) or request.META.get("REMOTE_ADDR")
    return ip.strip() if ip else None


def _dns_verify(ip, allowed_suffixes):
    """Reverse-then-forward DNS confirmation. True only if the IP's PTR
    hostname ends with an allowed suffix AND that hostname forward-resolves
    back to the same IP (covers IPv4 and IPv6)."""
    timeout = getattr(settings, "CRAWLER_DNS_TIMEOUT", 2.0)
    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        host = socket.gethostbyaddr(ip)[0].lower().rstrip(".")
        if not any(host.endswith(suffix) for suffix in allowed_suffixes):
            return False
        addrs = {info[4][0] for info in socket.getaddrinfo(host, None)}
        return ip in addrs
    except (socket.herror, socket.gaierror, OSError):
        return False
    finally:
        socket.setdefaulttimeout(old_timeout)


def is_verified_crawler(request):
    """True when the request is from a DNS-verified search crawler."""
    allowed_suffixes = _matched_suffixes(request.META.get("HTTP_USER_AGENT"))
    if not allowed_suffixes:
        return False  # ordinary visitors short-circuit here — no DNS work

    ip = _trusted_client_ip(request)
    if not ip:
        return False

    now = time.time()
    with _cache_lock:
        cached = _verify_cache.get(ip)
        if cached and cached[1] > now:
            return cached[0]

    verified = _dns_verify(ip, allowed_suffixes)

    ttl = (
        getattr(settings, "CRAWLER_VERIFY_TTL", 604800)      # 7 days
        if verified
        else getattr(settings, "CRAWLER_VERIFY_NEG_TTL", 3600)  # 1 hour
    )
    with _cache_lock:
        if len(_verify_cache) > _CACHE_MAX:
            expired = [k for k, v in _verify_cache.items() if v[1] <= now]
            for k in expired:
                del _verify_cache[k]
            if len(_verify_cache) > _CACHE_MAX:
                _verify_cache.clear()  # bound memory over a spoof flood
        _verify_cache[ip] = (verified, now + ttl)
    return verified
