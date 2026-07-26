"""
Server-side blocking of AI / LLM data-collection crawlers.

``Icarus/templates/robots.txt`` already asks these crawlers to stay out, but
robots.txt is advisory — a scraper is free to ignore it. This middleware
*enforces* the opt-out: any request whose ``User-Agent`` identifies as a known
AI crawler is answered with ``403 Forbidden`` before it ever reaches a view, so
no article body is served to it.

Genuine search-engine crawlers (Googlebot, Bingbot, …) are a *different*
category in :mod:`home.ua_utils` and are never matched here, so search indexing
and SEO are unaffected. The block list is the single source of truth in
``home.ua_utils._AI_BOTS`` (shared with the analytics classifier).

Configuration (both read from the environment in settings):
    AI_CRAWLER_BLOCK_ENABLED   toggle the whole block (default: True)
    AI_CRAWLER_ALLOWLIST       list of UA substrings to let through anyway,
                               e.g. ["ChatGPT-User"] to permit user-initiated
                               fetches while still blocking bulk trainers.
"""
import logging

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponseForbidden
from django.utils.deprecation import MiddlewareMixin

from home.ua_utils import ai_bot_name

logger = logging.getLogger(__name__)

# Kept readable even for blocked bots so they can still fetch the robots.txt
# opt-out and back off politely.
_EXEMPT_PATHS = ("/robots.txt",)

_FORBIDDEN_BODY = (
    "AI and LLM data-collection crawlers are not permitted on this site. "
    "See /robots.txt\n"
)

# Best-effort, process-local counter surfaced on the analytics dashboard. Not
# authoritative (per-worker, resets on restart) — server logs are the record.
_BLOCK_COUNT_KEY = "ai_crawler_blocked_total"


class BlockAICrawlersMiddleware(MiddlewareMixin):
    """Reject known AI crawlers with a 403 before they reach any view."""

    def process_request(self, request):
        if not getattr(settings, "AI_CRAWLER_BLOCK_ENABLED", True):
            return None
        if request.path in _EXEMPT_PATHS:
            return None

        ua = request.META.get("HTTP_USER_AGENT", "")
        if not ua:
            return None

        name = ai_bot_name(ua)
        if not name:
            return None  # humans and search crawlers fall through here

        ua_lower = ua.lower()
        for token in getattr(settings, "AI_CRAWLER_ALLOWLIST", ()) or ():
            if token and token.lower() in ua_lower:
                return None

        try:
            cache.add(_BLOCK_COUNT_KEY, 0)
            cache.incr(_BLOCK_COUNT_KEY)
        except Exception:
            pass

        logger.warning(
            "Blocked AI crawler %s from %s on %s (UA: %s)",
            name,
            request.META.get("REMOTE_ADDR", "?"),
            request.path,
            ua[:200],
        )
        return HttpResponseForbidden(_FORBIDDEN_BODY, content_type="text/plain")
