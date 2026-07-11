"""
Icarus/protected_media.py
─────────────────────────────────────────────────────────────────────────────
Caddy forward_auth endpoints.

These views are called as internal subrequests by Caddy's forward_auth
directive.  They inspect the session carried by the browser cookie (Django
middleware runs normally), then return:

    200 OK          → Caddy serves the file directly from disk (fast path)
    403 Forbidden   → Caddy returns 403 to the browser
    404 Not Found   → Caddy returns 404 to the browser

IMPORTANT: none of these endpoints are reachable from the public internet —
the Caddyfile contains `handle /internal/* { respond "Not Found" 404 }` which
is evaluated *before* the reverse_proxy, so external probes are blocked.
"""
import logging
from pathlib import Path
from urllib.parse import unquote

from django.conf import settings
from django.http import HttpResponse

from postbox.models import Postbox

logger = logging.getLogger(__name__)

# ── Shared helper ──────────────────────────────────────────────────────────


def _safe_target(raw_uri: str, media_subdir: str):
    """
    Resolve the file the browser originally requested into an absolute path
    that is guaranteed to live inside MEDIA_ROOT/<media_subdir>/.

    Steps (order matters):
      1. Strip query string on the raw bytes *before* decoding — this prevents
         a crafted '%3F' from being decoded to '?' and then confusing the split.
      2. URL-decode the path component.
      3. Resolve the absolute path and verify containment with is_relative_to()
         (stronger than startswith — immune to "/foo/../bar" tricks).

    Returns (target_path, base_dir) on success, or raises ValueError with a
    safe log message.  The caller converts ValueError → 404.
    """
    # Step 1: strip query string on the raw (still-encoded) URI
    path_only = raw_uri.split("?", 1)[0]
    # Step 2: percent-decode
    decoded = unquote(path_only)

    prefix = f"/media/{media_subdir}/"
    if not decoded.startswith(prefix):
        raise ValueError(f"URI '{decoded}' does not start with expected prefix '{prefix}'")

    rel_path = decoded[len(prefix):]

    # Step 3: containment check using pathlib (immune to traversal)
    base_dir = (Path(settings.MEDIA_ROOT) / media_subdir).resolve()
    target_path = (base_dir / rel_path).resolve()

    try:
        target_path.relative_to(base_dir)  # raises ValueError if outside
    except ValueError:
        raise ValueError(
            f"Path traversal attempt: resolved '{target_path}' is outside '{base_dir}'"
        )

    return target_path, base_dir


# ── archive_authz ──────────────────────────────────────────────────────────


def archive_authz(request):
    """
    Caddy forward_auth endpoint for /media/archive/*.

    Access policy: active subscriber OR staff/superuser.
    (Thumbnails at /media/archive/thumbnails/* are served publicly and never
    reach this endpoint — the Caddyfile handles them with a more-specific rule
    evaluated first.)
    """
    # 1. Require a subscriber or admin
    user = request.user
    if not user.is_authenticated:
        return HttpResponse(status=403)

    is_privileged = user.is_staff or user.is_superuser
    is_subscribed = getattr(user, "is_subscribed", False)

    if not (is_privileged or is_subscribed):
        return HttpResponse(status=403)

    # 2. Validate the target path
    raw_uri = (
        request.headers.get("X-Forwarded-Uri")
        or request.META.get("HTTP_X_FORWARDED_URI", "")
    )
    if not raw_uri:
        logger.warning("archive_authz: missing X-Forwarded-Uri header")
        return HttpResponse(status=400)

    try:
        target_path, _ = _safe_target(raw_uri, "archive")
    except ValueError as exc:
        logger.warning("archive_authz: %s", exc)
        return HttpResponse(status=404)

    if not target_path.is_file():
        return HttpResponse(status=404)

    return HttpResponse(status=200)


# ── postbox_authz ──────────────────────────────────────────────────────────


def postbox_authz(request):
    """
    Caddy forward_auth endpoint for /media/postbox_images/*.

    Access policy: staff member OR the user who submitted the feedback that
    contains this image (ownership check via DB).
    """
    # 1. Require authentication
    if not request.user.is_authenticated:
        return HttpResponse(status=403)

    # 2. Validate the target path
    raw_uri = (
        request.headers.get("X-Forwarded-Uri")
        or request.META.get("HTTP_X_FORWARDED_URI", "")
    )
    if not raw_uri:
        logger.warning("postbox_authz: missing X-Forwarded-Uri header")
        return HttpResponse(status=400)

    try:
        target_path, base_dir = _safe_target(raw_uri, "postbox_images")
    except ValueError as exc:
        logger.warning("postbox_authz: %s", exc)
        return HttpResponse(status=404)

    if not target_path.is_file():
        return HttpResponse(status=404)

    # 3. Staff can see any feedback image
    if request.user.is_staff or request.user.is_superuser:
        return HttpResponse(status=200)

    # 4. Non-staff: require ownership — the image must be linked to a Postbox
    #    record that belongs to this user.
    rel_path = target_path.relative_to(Path(settings.MEDIA_ROOT))
    db_path = str(rel_path)  # e.g. "postbox_images/abc.jpg"

    if not Postbox.objects.filter(user=request.user, image=db_path).exists():
        return HttpResponse(status=403)

    return HttpResponse(status=200)


# ── page_cache_authz ───────────────────────────────────────────────────────


def page_cache_authz(request):
    """
    Caddy forward_auth endpoint for /media/page_cache/*.

    Page images are pre-rendered by a Django-Q2 background task when a PDF is
    published.  This endpoint is a PURE PERMISSION CHECK — it never renders
    anything.  A missing file is a genuine 404 (meaning the pre-render task
    did not run; fix it upstream, don't mask it here).

    Cache layout:
        MEDIA_ROOT/page_cache/<source_key>/0001.webp
        MEDIA_ROOT/page_cache/<source_key>/meta.json

    Access policy: the page_cache mirrors the archive's access rules.
        • ArchiveIssue pages  → subscriber OR staff/superuser
        • Standalone ArchiveDocument pages → any authenticated user

    Because the <source_key> is an opaque hash (see page_cache.source_key()),
    we cannot reverse it back to a specific PDF without a DB lookup.  We take
    the conservative approach: require subscriber-or-staff for all page_cache
    requests.  If you later need to differentiate, store a source_key→pdf_path
    mapping in the DB or a fast cache (e.g. Redis/memcached).
    """
    user = request.user
    if not user.is_authenticated:
        return HttpResponse(status=403)

    is_privileged = user.is_staff or user.is_superuser
    is_subscribed = getattr(user, "is_subscribed", False)

    if not (is_privileged or is_subscribed):
        return HttpResponse(status=403)

    # Validate the target path — a missing file is a genuine 404 (bug upstream)
    raw_uri = (
        request.headers.get("X-Forwarded-Uri")
        or request.META.get("HTTP_X_FORWARDED_URI", "")
    )
    if not raw_uri:
        logger.warning("page_cache_authz: missing X-Forwarded-Uri header")
        return HttpResponse(status=400)

    try:
        target_path, _ = _safe_target(raw_uri, "page_cache")
    except ValueError as exc:
        logger.warning("page_cache_authz: %s", exc)
        return HttpResponse(status=404)

    if not target_path.is_file():
        # Pre-render task did not complete — do not lazy-render here.
        logger.warning("page_cache_authz: cache miss at '%s' — pre-render task may not have run", target_path)
        return HttpResponse(status=404)

    return HttpResponse(status=200)
