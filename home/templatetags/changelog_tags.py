"""
home/templatetags/changelog_tags.py
─────────────────────────────────────────────────────────────────────────────
Renders the "what's new" floating banner on the home page from changelog.json
at the repo root.

Design notes:

  • The changelog is a CURATED, reader-facing, Malayalam file — deliberately
    not the git log (which is English, technical, and one-fix-per-commit
    granular). The banner appears only when a new entry is written, so
    deploys without reader-visible changes stay silent automatically.

  • lru_cache is correct here BECAUSE of the deploy model: changelog.json is
    baked into the image (COPY . .) and cannot change during a container's
    lifetime, and every redeploy is a fresh process — so the cache
    invalidates itself for free. In dev, restart runserver after editing.

  • Entries are dicts, so `{{ entry.items }}` in the template is safe despite
    dict.items() existing: Django's template variable resolution tries
    dictionary KEY lookup before attribute lookup.

  • When the /en/ site ships, add optional `title_en` / `items_en` fields
    here and select on django.utils.translation.get_language() — release
    notes are content, not UI strings, so they bypass gettext.

Entry shape (newest first):
    {"id": "...", "date": "YYYY-MM-DD", "title": "...", "items": ["...", ...]}

The `id` doubles as the localStorage dismissal key on the client, so it must
change with every new entry (date-plus-slug works well).
"""
import json
import logging
from functools import lru_cache
from pathlib import Path

from django import template
from django.conf import settings

logger = logging.getLogger(__name__)

register = template.Library()


@lru_cache(maxsize=1)
def _latest_entry():
    """Return the newest changelog entry, or None (missing/invalid file →
    banner simply doesn't render; never break the home page over this)."""
    path = Path(settings.BASE_DIR) / "changelog.json"
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("changelog.json unreadable, banner disabled: %s", exc)
        return None
    if not isinstance(entries, list) or not entries:
        return None
    entry = entries[0]
    # Minimal shape check so a malformed top entry degrades to "no banner"
    # instead of a template error on every home page view.
    if not isinstance(entry, dict) or not entry.get("id") or not entry.get("title"):
        logger.warning("changelog.json top entry malformed, banner disabled")
        return None
    return entry


@register.inclusion_tag("home/partials/changelog_banner.html")
def changelog_banner():
    return {"entry": _latest_entry()}