"""
home/theme.py
─────────────────────────────────────────────────────────────────────────
Site-wide design themes.

The site can be shown in one of two designs, picked by an admin under
Wagtail admin → Settings → Site theme:

    classic   the design the site has always had (the default)
    print     the print-edition redesign: a black masthead band, plain
              paper, print-style page numbers and one colour per issue

Nothing about the content changes between themes; only templates and CSS
do.  A page template is swapped by looking for a copy of it under
`themes/<theme>/` first, so a page with no redesigned template simply
keeps its classic one (inside the new header, footer and colours, which
base.html switches on its own).

Staff can try a theme before switching the whole site: adding
`?theme_preview=print` to any URL shows that theme to them alone, for
the rest of their session.  `?theme_preview=off` ends the preview.

The second half of this module turns an issue's colour into the shades
the print theme uses: the colour exactly as picked wherever it is a
background or decoration, and a readable shade of it wherever it is text.
For the design's own colours (cobalt, teal, violet) that is exactly the
mock-ups.
"""
import re

from django.core.validators import RegexValidator

THEME_CLASSIC = 'classic'
THEME_PRINT = 'print'

THEME_CHOICES = [
    (THEME_CLASSIC, 'Classic'),
    (THEME_PRINT, 'Print edition — black masthead, one colour per issue'),
]
THEMES = {key for key, _ in THEME_CHOICES}

PREVIEW_PARAM = 'theme_preview'
PREVIEW_SESSION_KEY = 'site_theme_preview'

# The August 2025 cobalt: used for any issue that has no colour of its own
# and the site setting has none either.
DEFAULT_ISSUE_COLOUR = '#1d5fc4'

hex_colour_validator = RegexValidator(
    r'^#[0-9a-fA-F]{6}$',
    'Enter a colour like #1d5fc4 or #fc0, or pick one with the swatch.',
)

_HEX = re.compile(r'^#?([0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$')
_RGB = re.compile(r'^rgba?\(\s*(\d{1,3})[\s,]+(\d{1,3})[\s,]+(\d{1,3})\s*(?:[,/]\s*[\d.]+%?\s*)?\)$')


def normalise_colour(value):
    """
    A colour as typed or pasted, written the one way the site stores it:
    '#FC0', 'fc0', ' FFCC00 ', '#ffcc0080' and 'rgb(255, 204, 0)' all
    become '#ffcc00'.  Empty stays empty.  Anything else is returned as
    typed, so the validator can say what is wrong with it.  (Colour names
    like 'gold' are turned into hex by the admin's colour box before the
    form is sent.)
    """
    if not isinstance(value, str):
        return value
    text = value.strip().lower()
    if not text:
        return ''
    match = _HEX.match(text)
    if match:
        digits = match.group(1)
        if len(digits) in (3, 4):           # #rgb, #rgba → #rrggbb
            digits = ''.join(d * 2 for d in digits[:3])
        return '#' + digits[:6]             # drop any alpha
    match = _RGB.match(text)
    if match:
        channels = [int(c) for c in match.groups()]
        if all(c <= 255 for c in channels):
            return to_hex(channels)
    return value.strip()


# ── Which theme is this request in? ─────────────────────────────────────

def _site_setting(request):
    """The admin's choice, or None when it can't be read for this request."""
    from wagtail.models import Site
    from home.models import SiteThemeSettings

    site = Site.find_for_request(request)
    if site is None:
        return None
    return SiteThemeSettings.for_site(site)


def _handle_preview(request, theme):
    """
    Apply (and remember) a staff member's theme preview.

    Only staff can preview: an ordinary reader adding ?theme_preview=print
    gets the site's real theme, so the preview can't leak into links shared
    with the public.
    """
    user = getattr(request, 'user', None)
    session = getattr(request, 'session', None)
    if session is None or user is None or not user.is_authenticated or not user.is_staff:
        return theme, False

    asked = request.GET.get(PREVIEW_PARAM)
    if asked is not None:
        if asked in THEMES:
            session[PREVIEW_SESSION_KEY] = asked
        else:
            session.pop(PREVIEW_SESSION_KEY, None)

    previewing = session.get(PREVIEW_SESSION_KEY)
    if previewing in THEMES:
        return previewing, True
    return theme, False


def get_theme(request):
    """
    The theme this request is shown in.  Cached on the request, so the
    template switch and every template tag share one settings lookup.
    """
    if request is None:
        return THEME_CLASSIC

    cached = getattr(request, '_site_theme', None)
    if cached is not None:
        return cached

    settings_obj = _site_setting(request)
    theme = getattr(settings_obj, 'theme', None)
    if theme not in THEMES:
        theme = THEME_CLASSIC

    theme, previewing = _handle_preview(request, theme)

    request._site_theme = theme
    request._site_theme_previewing = previewing
    request._site_theme_settings = settings_obj
    return theme


def is_previewing(request):
    get_theme(request)
    return bool(getattr(request, '_site_theme_previewing', False))


def default_issue_colour(request):
    get_theme(request)
    settings_obj = getattr(request, '_site_theme_settings', None)
    colour = getattr(settings_obj, 'default_issue_colour', '') or ''
    return colour if parse_hex(colour) else DEFAULT_ISSUE_COLOUR


def themed_template(request, template_name):
    """
    The template names to try for `template_name` in the request's theme,
    most specific first.  Django's TemplateResponse and select_template
    take the list as-is, so a missing themed copy falls back silently.
    """
    names = [template_name] if isinstance(template_name, str) else list(template_name)
    theme = get_theme(request)
    if theme == THEME_CLASSIC:
        return names
    return [f'themes/{theme}/{name}' for name in names] + names


class ThemedTemplateMixin:
    """
    For Wagtail page models: serve `themes/<theme>/<template>` when the
    site's theme has its own version of this page's template.

    Put it before `Page` in the bases so it wraps Page.get_template.
    """

    def get_template(self, request, *args, **kwargs):
        template = super().get_template(request, *args, **kwargs)
        return themed_template(request, template)


# ── Issue colour → the shades the print theme uses ──────────────────────
#
# The design handoff derives every accent from one text-safe shade: the
# picked colour darkened in 10% steps toward black until it has 4.5:1
# contrast on the paper.  That keeps text readable, but it also turned
# every light or bright colour (yellow, orange, lime, pink, sky blue) into
# a muddy dark version everywhere it appeared, stripes and backgrounds
# included.  So the colour is split by what it is used for:
#
#   --issue-fill      the picked colour, exactly: stripes' base, tags,
#                     buttons, panel top borders, covers, progress bars
#   --on-issue        white or ink, whichever reads better on --issue-fill
#   --issue           text on the paper (links, labels, small type):
#                     darkened only as far as needed for 4.5:1
#   --issue-display   large text on the paper (headlines, page numbers,
#                     big numerals): darkened only as far as needed for
#                     3:1, the WCAG level for large text
#   --issue-dark      --issue mixed 30% toward black (bylines)
#   --issue-bright    the picked colour mixed 25% toward white (the 8px
#                     stripes, square markers)
#   --issue-light     the picked colour mixed 60% toward white (headings
#                     on the black band)
#   --issue-on-band   text in the issue colour on the black band,
#                     lightened only as far as needed for 4.5:1
#   --issue-on-white  text on a white button in any reader theme
#   --on-issue-accent accent text on an --on-issue chip (the subscribe
#                     box label)
# "Mixed" is plain linear interpolation of the sRGB channels.
#
# A colour that is already dark enough (the design's cobalt, teal, violet)
# comes out exactly as in the design.  For the dusk and dark reader themes
# the text shades are *lightened* the same way until they read on the dark
# page (dark_issue, dark_issue_display, dark_issue_dark).

PAPER_LIGHT = '#f3f4f0'
PAPER_DUSK = '#16130e'
BAND = '#15171c'
INK = '#1a1612'
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
MAX_STEPS = 12
MIN_CONTRAST = 4.5          # WCAG AA, normal text
MIN_CONTRAST_LARGE = 3.0    # WCAG AA, large text (24px, or 18.7px bold)


def parse_hex(value):
    """'#1d5fc4' → (29, 95, 196); anything else → None."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if len(value) != 7 or not value.startswith('#'):
        return None
    try:
        return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))
    except ValueError:
        return None


def to_hex(rgb):
    return '#' + ''.join(
        f'{max(0, min(255, round(channel))):02x}' for channel in rgb
    )


def mix(rgb, target, t):
    return tuple(c + (target[i] - c) * t for i, c in enumerate(rgb))


def luminance(rgb):
    """WCAG relative luminance."""
    def linear(channel):
        channel /= 255
        if channel <= 0.03928:
            return channel / 12.92
        return ((channel + 0.055) / 1.055) ** 2.4
    r, g, b = (linear(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = luminance(a), luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def _text_safe(rgb, paper, toward, minimum=MIN_CONTRAST):
    """
    `rgb` moved toward `toward` in 10% steps until it reads on `paper`.
    Each step is rounded to whole RGB values, so the colour that is
    checked is the colour that is written out.
    """
    for _ in range(MAX_STEPS):
        if contrast(rgb, paper) >= minimum:
            break
        rgb = tuple(round(c) for c in mix(rgb, toward, 0.1))
    return rgb


def text_on(fill):
    """
    The text colour for a `fill` background: white (as in the design) when
    it reads, else the site's ink, else pure black for the few mid-tones
    (a bright red) where even ink falls short.  One of white and black
    always reaches 4.5:1.
    """
    for candidate in (WHITE, parse_hex(INK)):
        if contrast(fill, candidate) >= MIN_CONTRAST:
            return candidate
    return BLACK if contrast(fill, BLACK) > contrast(fill, WHITE) else WHITE


def issue_palette(colour, fallback=DEFAULT_ISSUE_COLOUR):
    """
    Every shade the print theme needs for one issue colour, as hex
    strings (see the table above).  The dark_* keys are the text shades
    for the dusk and dark reader themes.
    """
    rgb = parse_hex(colour) or parse_hex(fallback) or parse_hex(DEFAULT_ISSUE_COLOUR)
    paper, dusk = parse_hex(PAPER_LIGHT), parse_hex(PAPER_DUSK)

    text = _text_safe(rgb, paper, BLACK)
    display = _text_safe(rgb, paper, BLACK, MIN_CONTRAST_LARGE)
    on_dark = _text_safe(rgb, dusk, WHITE)
    on_dark_display = _text_safe(rgb, dusk, WHITE, MIN_CONTRAST_LARGE)
    on_fill = text_on(rgb)
    dark = mix(text, BLACK, 0.3)

    return {
        'picked': to_hex(rgb),
        'issue': to_hex(text),
        'issue_display': to_hex(display),
        'issue_dark': to_hex(dark),
        'issue_fill': to_hex(rgb),
        'on_issue': to_hex(on_fill),
        # On a white chip, the darker text shade; on an ink chip, the colour itself.
        'on_issue_accent': to_hex(dark if on_fill == WHITE else rgb),
        'issue_bright': to_hex(mix(rgb, WHITE, 0.25)),
        'issue_light': to_hex(mix(rgb, WHITE, 0.6)),
        'issue_on_band': to_hex(_text_safe(rgb, parse_hex(BAND), WHITE)),
        'issue_on_white': to_hex(text),
        'dark_issue': to_hex(on_dark),
        'dark_issue_display': to_hex(on_dark_display),
        'dark_issue_dark': to_hex(mix(on_dark, WHITE, 0.3)),
    }


# Generated archive covers for issues that have no colour of their own
# (print-only back issues).  Same set and rotation as the design; all take
# white text.
COVER_PALETTE = [
    ('#15171c', '#6f9bea'),
    ('#1d5fc4', '#a9c3ef'),
    ('#0e7c83', '#9fdde0'),
    ('#7b3fb0', '#d4b6ee'),
    ('#9c4f10', '#f2c49a'),
]


def cover_colours(year, month, colour=None):
    """(background, stripe, text colour) for a generated archive cover."""
    if parse_hex(colour):
        palette = issue_palette(colour)
        return palette['issue_fill'], palette['issue_light'], palette['on_issue']
    background, stripe = COVER_PALETTE[((month or 0) + (year or 0)) % len(COVER_PALETTE)]
    return background, stripe, '#ffffff'


# ── Dates in the reader's language ───────────────────────────────────────

ML_MONTHS = [
    'ജനുവരി', 'ഫെബ്രുവരി', 'മാർച്ച്', 'ഏപ്രിൽ', 'മേയ്', 'ജൂൺ',
    'ജൂലൈ', 'ആഗസ്റ്റ്', 'സെപ്റ്റംബർ', 'ഒക്ടോബർ', 'നവംബർ', 'ഡിസംബർ',
]
EN_MONTHS = [
    'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December',
]


def month_name(month, language=None):
    """Malayalam month name, or English on the English site (/en/)."""
    from django.utils.translation import get_language

    language = language or get_language() or 'ml'
    names = EN_MONTHS if language.startswith('en') else ML_MONTHS
    try:
        return names[int(month) - 1]
    except (TypeError, ValueError, IndexError):
        return ''


def month_year(value, language=None):
    """A date → 'ആഗസ്റ്റ് 2025' (or 'August 2025' on the English site)."""
    if not value:
        return ''
    return f'{month_name(value.month, language)} {value.year}'


def next_month(value):
    """(year, month) of the month after `value`."""
    if value.month == 12:
        return value.year + 1, 1
    return value.year, value.month + 1
