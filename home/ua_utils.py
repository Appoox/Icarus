"""
Lightweight, dependency-free User-Agent classification for the analytics
dashboard.

``django-hitcount`` records the raw ``User-Agent`` string on every ``Hit`` row
but never interprets it, so the admin analytics page has no way to tell a human
reader apart from Googlebot or a ``python-requests`` scraper. This module turns
a raw UA string into a coarse ``{category, bot_name, device, browser, os}``
summary using pure string matching — no DNS, no third-party service, no extra
dependency — so it is cheap enough to run over thousands of hit rows on a single
dashboard request.

For the paywall path there is a separate, *stricter* check in
``articles/crawler_utils.py`` that additionally confirms a claimed search
crawler with reverse-then-forward DNS. That verification is deliberately not
reused here: this classifier only reports what a UA *claims* to be (which is all
the analytics view needs) and must stay fast in bulk. The search-engine tokens
below mirror ``crawler_utils._CRAWLER_RDNS_SUFFIXES`` so the two modules agree on
what "a search crawler" looks like.
"""

CATEGORY_HUMAN = "human"
CATEGORY_SUSPECTED = "suspected_bot"
CATEGORY_SEARCH = "search_bot"
CATEGORY_AI = "ai_bot"
CATEGORY_SCRAPER = "scraper"
CATEGORY_SOCIAL = "social"
CATEGORY_FEED = "feed"
CATEGORY_UNKNOWN = "unknown"

CATEGORY_LABELS = {
    CATEGORY_HUMAN: "Human",
    CATEGORY_SUSPECTED: "Suspected bot / datacenter",
    CATEGORY_SEARCH: "Search bot",
    CATEGORY_AI: "AI scraper",
    CATEGORY_SCRAPER: "Scraper / automation",
    CATEGORY_SOCIAL: "Social preview",
    CATEGORY_FEED: "Feed reader",
    CATEGORY_UNKNOWN: "Unknown",
}

# Fixed order used by the dashboard doughnut / stacked values. Note:
# ``classify_user_agent`` never emits CATEGORY_SUSPECTED — that verdict is
# produced where the client IP is known (home.hit_filters / the analytics view)
# by combining a human/unknown UA with a datacenter IP.
CATEGORY_ORDER = (
    CATEGORY_HUMAN, CATEGORY_SUSPECTED, CATEGORY_SEARCH, CATEGORY_AI,
    CATEGORY_SCRAPER, CATEGORY_SOCIAL, CATEGORY_FEED, CATEGORY_UNKNOWN,
)

# Search-engine crawlers: lowercase UA substring -> display name. These index
# content for a search engine (Google/Bing/…), so they are allowed through the
# AI block — do NOT add AI/LLM crawlers here (see _AI_BOTS below).
_SEARCH_BOTS = (
    ("googlebot", "Googlebot"),
    ("google-inspectiontool", "Google InspectionTool"),
    ("storebot-google", "Googlebot"),
    ("bingbot", "Bingbot"),
    ("bingpreview", "Bingbot"),
    ("adidxbot", "Bingbot (AdIdx)"),
    ("yandexbot", "YandexBot"),
    ("duckduckbot", "DuckDuckBot"),
    ("baiduspider", "Baidu Spider"),
    ("applebot", "Applebot"),
    ("petalbot", "PetalBot"),
)

# AI / LLM data-collection crawlers: lowercase UA substring -> display name.
# These scrape content to train or feed generative-AI models; they are NOT
# search-engine indexers, so blocking them does not affect Google/Bing rankings.
# This tuple is the single source of truth shared with:
#   * the analytics classifier (below) — labels this traffic "AI scraper", and
#   * home.ai_crawler_middleware — returns 403 to these user-agents,
# and it mirrors the AI groups in Icarus/templates/robots.txt. Note that
# Google-Extended / Applebot-Extended are robots.txt-only opt-out tokens (they
# are never sent as a User-Agent — the plain Googlebot/Applebot UA is used), so
# they intentionally do not appear here and cannot be UA-blocked without also
# blocking search.
_AI_BOTS = (
    ("gptbot", "GPTBot (OpenAI)"),
    ("oai-searchbot", "OAI-SearchBot (OpenAI)"),
    ("chatgpt-user", "ChatGPT-User (OpenAI)"),
    ("claudebot", "ClaudeBot (Anthropic)"),
    ("claude-web", "Claude-Web (Anthropic)"),
    ("anthropic-ai", "Anthropic AI"),
    ("perplexitybot", "PerplexityBot"),
    ("perplexity-user", "Perplexity-User"),
    ("ccbot", "CCBot (Common Crawl)"),
    ("bytespider", "Bytespider (ByteDance)"),
    ("amazonbot", "Amazonbot"),
    ("meta-externalagent", "Meta-ExternalAgent"),
    ("facebookbot", "FacebookBot"),
    ("cohere-ai", "Cohere AI"),
    ("diffbot", "Diffbot"),
    ("omgilibot", "Omgilibot"),
    ("youbot", "YouBot"),
    ("timpibot", "Timpibot"),
    ("imagesiftbot", "ImagesiftBot"),
    ("img2dataset", "img2dataset"),
    ("google-cloudvertexbot", "Google CloudVertexBot"),
)

# Social / messaging link-preview fetchers.
_SOCIAL_BOTS = (
    ("facebookexternalhit", "Facebook"),
    ("facebookcatalog", "Facebook"),
    ("twitterbot", "Twitterbot"),
    ("linkedinbot", "LinkedInBot"),
    ("slackbot", "Slackbot"),
    ("telegrambot", "TelegramBot"),
    ("whatsapp", "WhatsApp"),
    ("discordbot", "Discordbot"),
    ("pinterest", "Pinterest"),
    ("redditbot", "Redditbot"),
    ("skypeuripreview", "Skype"),
)

# Feed readers / aggregators.
_FEED_BOTS = (
    ("feedly", "Feedly"),
    ("feedfetcher", "Feedfetcher"),
    ("inoreader", "Inoreader"),
    ("newsblur", "NewsBlur"),
    ("feedbin", "Feedbin"),
)

# Generic scraper / automation tooling and SEO crawlers. A match here (when
# nothing more specific matched) is reported as a scraper.
_SCRAPER_TOKENS = (
    ("python-requests", "python-requests"),
    ("python-urllib", "urllib"),
    ("aiohttp", "aiohttp"),
    ("httpx", "httpx"),
    ("scrapy", "Scrapy"),
    ("curl", "curl"),
    ("wget", "Wget"),
    ("go-http-client", "Go http"),
    ("okhttp", "OkHttp"),
    ("java/", "Java"),
    ("libwww-perl", "libwww-perl"),
    ("headlesschrome", "Headless Chrome"),
    ("phantomjs", "PhantomJS"),
    ("puppeteer", "Puppeteer"),
    ("playwright", "Playwright"),
    ("selenium", "Selenium"),
    ("axios", "axios"),
    ("node-fetch", "node-fetch"),
    ("apache-httpclient", "Apache HttpClient"),
    ("semrushbot", "SemrushBot"),
    ("ahrefsbot", "AhrefsBot"),
    ("mj12bot", "MJ12bot"),
    ("dotbot", "DotBot"),
    ("dataforseobot", "DataForSeoBot"),
    ("bytespider", "Bytespider"),
    ("censysinspect", "Censys"),
    ("masscan", "masscan"),
    ("zgrab", "zgrab"),
)

# Last-resort generic markers that just mean "some kind of bot".
_GENERIC_BOT_TOKENS = ("bot", "spider", "crawler", "crawl", "scraper")


def _match(ua, table):
    for token, name in table:
        if token in ua:
            return name
    return None


def _browser(ua):
    # Order matters: Edge/Opera/Samsung all also advertise "chrome"/"safari".
    if "edg/" in ua or "edga/" in ua or "edgios/" in ua:
        return "Edge"
    if "opr/" in ua or "opera" in ua:
        return "Opera"
    if "samsungbrowser" in ua:
        return "Samsung Internet"
    if "firefox" in ua or "fxios" in ua:
        return "Firefox"
    if "chrome" in ua or "crios" in ua:
        return "Chrome"
    if "safari" in ua:
        return "Safari"
    return ""


def _os(ua):
    if "android" in ua:
        return "Android"
    if "iphone" in ua or "ipad" in ua or "ipod" in ua:
        return "iOS"
    if "windows" in ua:
        return "Windows"
    if "mac os x" in ua or "macintosh" in ua:
        return "macOS"
    if "cros" in ua:
        return "ChromeOS"
    if "linux" in ua:
        return "Linux"
    return ""


def _device(ua):
    if "ipad" in ua or "tablet" in ua:
        return "Tablet"
    if "mobile" in ua or "iphone" in ua or "ipod" in ua or "android" in ua:
        return "Mobile"
    return "Desktop"


def classify_user_agent(user_agent):
    """Return a coarse summary of a raw User-Agent string.

    Returns a dict with keys:
        category: one of the CATEGORY_* constants
        bot_name: human-readable bot name, or '' for humans/unknown
        device:   'Desktop' | 'Mobile' | 'Tablet' | ''
        browser:  e.g. 'Chrome' | 'Safari' | ''
        os:       e.g. 'Android' | 'Windows' | ''
    Never raises.
    """
    ua = (user_agent or "").strip().lower()
    empty = {"bot_name": "", "device": "", "browser": "", "os": ""}

    if not ua:
        return {"category": CATEGORY_UNKNOWN, **empty}

    # Most-specific bot classes first. AI crawlers are checked before search so
    # e.g. GPTBot is never mislabelled as (or allowed in as) a search engine.
    name = _match(ua, _AI_BOTS)
    if name:
        return {"category": CATEGORY_AI, **{**empty, "bot_name": name}}

    name = _match(ua, _SEARCH_BOTS)
    if name:
        return {"category": CATEGORY_SEARCH, **{**empty, "bot_name": name}}

    name = _match(ua, _SOCIAL_BOTS)
    if name:
        return {"category": CATEGORY_SOCIAL, **{**empty, "bot_name": name}}

    name = _match(ua, _FEED_BOTS)
    if name:
        return {"category": CATEGORY_FEED, **{**empty, "bot_name": name}}

    name = _match(ua, _SCRAPER_TOKENS)
    if name:
        return {"category": CATEGORY_SCRAPER, **{**empty, "bot_name": name}}

    # Generic catch-all bot markers (checked before browser sniffing so a UA
    # like "…bot…" isn't mistaken for a human just because it says "Mozilla").
    for token in _GENERIC_BOT_TOKENS:
        if token in ua:
            return {"category": CATEGORY_SCRAPER, **{**empty, "bot_name": "Other bot"}}

    # Looks like a real browser only if it advertises a known engine.
    browser = _browser(ua)
    if browser or "mozilla" in ua:
        return {
            "category": CATEGORY_HUMAN,
            "bot_name": "",
            "device": _device(ua),
            "browser": browser or "Other",
            "os": _os(ua),
        }

    # A non-browser UA that isn't an obvious bot — treat as unknown.
    return {"category": CATEGORY_UNKNOWN, **empty}


def ai_bot_name(user_agent):
    """Return the AI-crawler display name if the UA is a known AI/LLM
    data-collection crawler, else ``None``.

    Shared source of truth with :mod:`home.ai_crawler_middleware`, so that what
    the dashboard labels an "AI scraper" and what the server actually blocks can
    never drift apart.
    """
    ua = (user_agent or "").lower()
    if not ua:
        return None
    return _match(ua, _AI_BOTS)


def is_ai_crawler(user_agent):
    """True when the UA identifies as a known AI/LLM data-collection crawler."""
    return ai_bot_name(user_agent) is not None
