"""
Template tags for the print-edition theme (see home/theme.py).

    {% load print_theme %}
    {% pt_palette_style %}            the issue colour as CSS custom properties
    {% pt_current_issue as issue %}   the latest live issue (one query per request)
    {% pt_nav as nav %}               the section nav: items, active item, current issue
    {{ date|month_year }}             'ആഗസ്റ്റ് 2025' ('August 2025' on /en/)
    {{ name|initials }}               the syllable shown in an avatar circle
"""
import unicodedata

from django import template
from django.apps import apps
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext as _, gettext_lazy

from home import theme

register = template.Library()


# ── Lookups cached on the request ───────────────────────────────────────

def _request_cache(context):
    request = context.get('request')
    if request is None:
        return {}
    cache = getattr(request, '_print_theme_cache', None)
    if cache is None:
        cache = {}
        try:
            request._print_theme_cache = cache
        except AttributeError:
            pass
    return cache


def current_issue_for(context):
    cache = _request_cache(context)
    if 'current_issue' not in cache:
        Issue = apps.get_model('issue', 'Issue')
        cache['current_issue'] = (
            Issue.objects.live()
            .select_related('topic', 'cover_image', 'volume')
            .order_by('-date_of_publishing')
            .first()
        )
    return cache['current_issue']


@register.simple_tag(takes_context=True)
def pt_current_issue(context):
    return current_issue_for(context)


def palette_issue_for(context):
    """
    Whose colour this page wears: an issue page its own, an article its
    issue's, and every other page the current issue's.  A view can force
    one by putting `pt_palette_issue` in the context.
    """
    if context.get('pt_palette_issue'):
        return context['pt_palette_issue']
    page = context.get('page')
    if page is not None:
        specific = getattr(page, 'specific', page)
        if specific.__class__.__name__ == 'Issue':
            return specific
        issue = getattr(specific, 'main_issue', None)
        if issue is not None:
            return issue
    return current_issue_for(context)


@register.simple_tag(takes_context=True)
def pt_palette(context, issue=None):
    """The shades for `issue` (or this page's issue) — see theme.issue_palette."""
    request = context.get('request')
    fallback = theme.default_issue_colour(request) if request else theme.DEFAULT_ISSUE_COLOUR
    if issue is None:
        issue = palette_issue_for(context)
    return theme.issue_palette(getattr(issue, 'colour', '') or '', fallback=fallback)


# The shades that are the same in every reader theme, and the text shades
# that the dusk and dark themes swap for lighter ones (see home/theme.py).
_FIXED_SHADES = (
    ('--issue-fill', 'issue_fill'),
    ('--on-issue', 'on_issue'),
    ('--on-issue-accent', 'on_issue_accent'),
    ('--issue-bright', 'issue_bright'),
    ('--issue-light', 'issue_light'),
    ('--issue-on-band', 'issue_on_band'),
    ('--issue-on-white', 'issue_on_white'),
)
_TEXT_SHADES = (
    # (property, light theme key, dusk/dark theme key)
    ('--issue', 'issue', 'dark_issue'),
    ('--issue-display', 'issue_display', 'dark_issue_display'),
    ('--issue-dark', 'issue_dark', 'dark_issue_dark'),
)


def _declarations(pairs):
    # Values are hex strings computed in theme.issue_palette, never raw
    # editor input, so they are safe to write into CSS.
    return format_html_join('', '{}:{};', pairs)


@register.simple_tag(takes_context=True)
def pt_palette_style(context):
    """
    A <style> block setting the issue colour's shades on :root, with the
    lighter text shades for the dusk and dark reader themes.
    """
    p = pt_palette(context)
    light = [(prop, p[key]) for prop, key in _FIXED_SHADES]
    light += [(prop, p[key]) for prop, key, _ in _TEXT_SHADES]
    dark = [(prop, p[key]) for prop, _, key in _TEXT_SHADES]
    return format_html(
        '<style id="pt-issue-colour">:root{{{}}}html[data-theme^="d"]{{{}}}</style>',
        _declarations(light), _declarations(dark),
    )


@register.simple_tag(takes_context=True)
def pt_issue_style(context, issue):
    """
    Inline custom properties for a block that wears another issue's
    colour (the next-issue band, a cover in a list of issues).  `issue`
    is an Issue or a hex colour.  Use as
    class="pt-own-colour" style="{% pt_issue_style issue %}".
    """
    request = context.get('request')
    fallback = theme.default_issue_colour(request) if request else theme.DEFAULT_ISSUE_COLOUR
    colour = getattr(issue, 'colour', issue) if issue is not None else ''
    p = theme.issue_palette(colour or '', fallback=fallback)
    # The text shades go in as --own-*; class="pt-own-colour" picks the
    # light or dark one for the reader's theme (see print/base.css).
    pairs = [(prop, p[key]) for prop, key in _FIXED_SHADES]
    pairs += [(f'--own{prop[1:]}', p[key]) for prop, key, _ in _TEXT_SHADES]
    pairs += [(f'--own{prop[1:]}-on-dark', p[key]) for prop, _, key in _TEXT_SHADES]
    return _declarations(pairs)


# ── Wordmark on the black band ──────────────────────────────────────────

@register.simple_tag(takes_context=True)
def pt_wordmark(context):
    """
    The ശാസ്ത്രഗതി wordmark to show on the dark band, and how:

        {'image': <Image>, 'invert': False}   the white wordmark from
                                              Settings → Site theme, or the
                                              header's dark-theme title image
        {'image': <Image>, 'invert': True}    the header's regular title
                                              image, turned light in CSS
        {'image': None}                       no image: show the title as text
    """
    cache = _request_cache(context)
    if 'wordmark' in cache:
        return cache['wordmark']

    from home.models import SiteHeader

    request = context.get('request')
    theme.get_theme(request)
    settings_obj = getattr(request, '_site_theme_settings', None)
    header = SiteHeader.objects.select_related(
        'site_title_image', 'site_title_image_dark',
    ).first()

    result = {'image': None, 'invert': False,
              'title': getattr(header, 'site_title', '') or 'ശാസ്ത്രഗതി'}
    if settings_obj is not None and settings_obj.masthead_wordmark_id:
        result['image'] = settings_obj.masthead_wordmark
    elif header is not None and header.site_title_image_dark_id:
        result['image'] = header.site_title_image_dark
    elif header is not None and header.site_title_image_id:
        result.update(image=header.site_title_image, invert=True)

    cache['wordmark'] = result
    return result


# ── Section nav ─────────────────────────────────────────────────────────

def _first_live_url(model_label, **filters):
    model = apps.get_model(*model_label.split('.'))
    qs = model.objects.live()
    page = qs.filter(**filters).first() if filters else None
    page = page or qs.first()
    return page.url if page else None


def _reverse(name):
    try:
        return reverse(name)
    except NoReverseMatch:
        return None


def _active_section(context, urls):
    if context.get('pt_section'):
        return context['pt_section']

    page = context.get('page')
    if page is not None:
        name = getattr(page, 'specific', page).__class__.__name__
        section = {
            'Issue': 'issues', 'IssueIndexPage': 'issues',
            'Article': 'articles', 'ArticleIndexPage': 'articles',
            'Literati': 'authors', 'AuthorIndexPage': 'authors',
        }.get(name)
        if section:
            return section

    # Otherwise the section whose address this page's address sits under,
    # the most specific one winning: /issues/topics/… is a topic page, not
    # an issue; /authors/most-read/ belongs to authors.
    request = context.get('request')
    path = getattr(request, 'path', '') or ''
    prefixes = dict(urls)
    topic_root = _reverse('most_read_topics')
    if topic_root:
        prefixes['topics'] = topic_root.rsplit('most-read/', 1)[0]
    matches = [
        (len(url), key) for key, url in prefixes.items()
        if url and url != '/' and path.startswith(url)
    ]
    return max(matches)[1] if matches else None


@register.simple_tag(takes_context=True)
def pt_nav(context):
    cache = _request_cache(context)
    if 'nav_urls' not in cache:
        cache['nav_urls'] = {
            'issues': _first_live_url('issue.IssueIndexPage', sort_by='latest'),
            'articles': _first_live_url('articles.ArticleIndexPage', sort_by='latest'),
            'topics': _reverse('most_read_topics'),
            'authors': _first_live_url('literati.AuthorIndexPage'),
            'archive': _reverse('the_librarian:archive_list'),
            'letters': _reverse('postbox:page'),
        }
    urls = cache['nav_urls']
    labels = [
        ('issues', _('ലക്കങ്ങൾ')),
        ('articles', _('ലേഖനങ്ങൾ')),
        ('topics', _('വിഷയങ്ങൾ')),
        ('authors', _('ലേഖകർ')),
        ('archive', _('ആർക്കൈവ്')),
        ('letters', _('കത്തുകൾ')),
    ]
    active = _active_section(context, urls)
    items = [
        {'key': key, 'label': label, 'url': urls[key], 'active': key == active}
        for key, label in labels
        if urls.get(key)
    ]
    return {'items': items, 'active': active, 'urls': urls,
            'current_issue': current_issue_for(context)}


# ── Editorial board modal ───────────────────────────────────────────────

BOARD_ROLE_LABELS = [
    ('editor', gettext_lazy('എഡിറ്റർ')),
    ('associate', gettext_lazy('അസോസിയേറ്റ് എഡിറ്റർ')),
    ('managing', gettext_lazy('മാനേജിംഗ് എഡിറ്റർ')),
]


@register.simple_tag
def pt_board(source):
    """
    Board members arranged for the modal: the named roles as
    role/name pairs, everyone else as one list of names.

    `source` is a list of membership rows (Issue.board_members) or the
    footer's editorial board dict ({'sections': [{'members': [...]}]}).
    """
    if isinstance(source, dict):
        rows = [m for section in source.get('sections', []) for m in section['members']]
    else:
        rows = list(source or [])

    roles = []
    for role, label in BOARD_ROLE_LABELS:
        people = [row.editor for row in rows if row.role == role]
        if people:
            roles.append({'label': str(label), 'people': people})
    members = [row.editor for row in rows if row.role == 'board']
    return {'roles': roles, 'members': members, 'empty': not rows}


# ── Filters ─────────────────────────────────────────────────────────────

@register.filter
def month_year(value):
    """A date → 'ആഗസ്റ്റ് 2025', or 'August 2025' on the English site."""
    return theme.month_year(value)


@register.filter
def month_name(value):
    return theme.month_name(value)


@register.filter
def next_month_year(value):
    """The month after a date, e.g. for 'next issue: സെപ്റ്റംബർ 2025'."""
    if not value:
        return ''
    year, month = theme.next_month(value)
    return f'{theme.month_name(month)} {year}'


VIRAMA = '\u0d4d'                # chandrakkala
JOINERS = {'\u200c', '\u200d'}  # zero-width non-joiner, joiner


def _first_syllable(word):
    """
    The first written syllable of a word: a letter plus its vowel signs,
    and any consonants joined to it through a virama — 'ഷാജി' → 'ഷാ',
    'ശ്രീലക്ഷ്മി' → 'ശ്രീ'.  Latin names just give their first letter.
    """
    out = ''
    for char in word:
        if not out:
            out = char
            continue
        joined = out[-1] in (VIRAMA,) or out[-1] in JOINERS
        if unicodedata.category(char).startswith('M') or char in JOINERS or joined:
            out += char
            continue
        break
    return out


@register.filter
def initials(name):
    """
    The syllable in an avatar circle.  Skips honorifics ('ഡോ.', 'Dr.')
    and the short spelled-out initials common in Malayalam names ('എൻ',
    'കെ', 'എസ്'), so 'ഡോ. എൻ ഷാജി' gives 'ഷാ' and 'ഡോ. ടി എസ് പ്രീത'
    gives 'പ്രീ'.  When every word is short, the last one is used.
    """
    words = [w for w in str(name or '').split() if w and not w.endswith('.')]
    if not words:
        words = [w for w in str(name or '').split() if w]
    for word in words:
        long_enough = len(word) > 1 if word.isascii() else len(word) > 3
        if long_enough:
            return _first_syllable(word).upper()
    return _first_syllable(words[-1]).upper() if words else ''


@register.filter
def pad2(value):
    """Page numbers print with two digits: 6 → '06'."""
    try:
        return f'{int(value):02d}'
    except (TypeError, ValueError):
        return value or ''
