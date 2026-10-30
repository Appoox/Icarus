"""
The archive page in the print edition theme (see home/theme.py).

One year at a time (`?year=YYYY`, so every year has its own link), with a
rail of recent years and older decades.  It lists both kinds of issue the
site knows about, merged by month:

    Issue pages     the issues published on the site — linked to their page,
                    with their cover and theme
    Archive Issues  scanned back issues — linked to the flipbook viewer

An issue with no cover image gets a generated cover in its own colour, or
in a fixed rotation of colours when it has none (print-only back issues).
"""
from django.apps import apps
from django.urls import reverse
from django.utils.translation import gettext as _

from home import theme

RAIL_YEARS = 10


def _cover_url(issue):
    image = getattr(issue, 'cover_image', None)
    if image is None:
        return None
    from wagtail.images.models import SourceImageIOError

    try:
        return image.get_rendition('width-400').url
    except SourceImageIOError:
        # A missing or unreadable original falls back to a generated cover
        # rather than taking the whole archive page down.
        return None


def _entries(default_colour):
    """Every known issue as {(year, month): entry}."""
    Issue = apps.get_model('issue', 'Issue')
    ArchiveIssue = apps.get_model('the_librarian', 'ArchiveIssue')

    entries = {}
    for scan in ArchiveIssue.objects.all():
        entries[(scan.year, scan.month)] = {
            'year': scan.year,
            'month': scan.month,
            'volume': scan.volume,
            'issue_number': scan.issue_number,
            'url': reverse('the_librarian:magazine_viewer', args=[scan.pk]),
            'cover_url': scan.thumbnail.url if scan.thumbnail else None,
            'theme': '',
            'colour': '',
            'price': None,
        }

    issues = (
        Issue.objects.live()
        .select_related('topic', 'volume', 'cover_image')
        .exclude(date_of_publishing__isnull=True)
    )
    for issue in issues:
        key = (issue.date_of_publishing.year, issue.date_of_publishing.month)
        entry = entries.setdefault(key, {
            'year': key[0], 'month': key[1],
            'volume': None, 'issue_number': None, 'cover_url': None,
        })
        entry.update({
            'url': issue.url,
            'theme': issue.topic.name if issue.topic_id else '',
            # Same colour the issue's own page wears.
            'colour': issue.colour or default_colour,
            'price': issue.price,
        })
        if issue.volume_id:
            entry['volume'] = issue.volume.number
        if issue.issue_number:
            entry['issue_number'] = issue.issue_number
        entry['cover_url'] = _cover_url(issue) or entry.get('cover_url')

    for entry in entries.values():
        entry['month_name'] = theme.month_name(entry['month'])
        entry['label'] = f"{entry['month_name']} {entry['year']}"
        entry['meta'] = ' · '.join(
            part for part in (
                f"{_('വാല്യം')} {entry['volume']}" if entry['volume'] else '',
                f"{_('ലക്കം')} {entry['issue_number']}" if entry['issue_number'] else '',
            ) if part
        )
        entry['cover_bg'], entry['cover_stripe'], entry['cover_on'] = theme.cover_colours(
            entry['year'], entry['month'], entry.get('colour'),
        )
    return entries


def _decade_groups(years):
    """
    Older years grouped by decade, newest first, each labelled the way the
    design does: a whole decade as '2000കൾ', a partial one as '2010–15'.
    Selecting a group opens its latest year.
    """
    by_decade = {}
    for year in years:
        by_decade.setdefault(year // 10 * 10, []).append(year)

    groups = []
    for decade in sorted(by_decade, reverse=True):
        members = sorted(by_decade[decade])
        low, high = members[0], members[-1]
        if low == high:
            label = str(low)
        elif low == decade and high == decade + 9:
            label = f'{decade}കൾ'
        else:
            label = f'{low}–{high % 100:02d}'
        groups.append({'label': label, 'year': high, 'years': members})
    return groups


def archive_context(request):
    entries = _entries(theme.default_issue_colour(request))
    years = sorted({year for year, _ in entries}, reverse=True)

    try:
        selected = int(request.GET.get('year', ''))
    except ValueError:
        selected = None
    if selected not in years:
        selected = years[0] if years else None

    issues = sorted(
        (e for e in entries.values() if e['year'] == selected),
        key=lambda e: e['month'],
        reverse=True,
    )
    volumes = sorted({e['volume'] for e in issues if e['volume']})

    rail = years[:RAIL_YEARS]
    decades = _decade_groups(years[RAIL_YEARS:])
    for group in decades:
        group['active'] = selected in group['years']

    at = years.index(selected) if selected in years else -1
    newer = years[at - 1] if at > 0 else None
    older = years[at + 1] if 0 <= at < len(years) - 1 else None

    return {
        'pt_section': 'archive',
        'years': rail,
        'decades': decades,
        'selected_year': selected,
        'issues': issues,
        'issue_count': len(issues),
        'volume_low': volumes[0] if volumes else None,
        'volume_high': volumes[-1] if volumes else None,
        'newer_year': newer,
        'older_year': older,
        'first_volume': min((e['volume'] for e in entries.values() if e['volume']), default=None),
        'last_volume': max((e['volume'] for e in entries.values() if e['volume']), default=None),
    }
