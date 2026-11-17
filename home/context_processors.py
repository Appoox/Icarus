from django.utils.functional import SimpleLazyObject

from home import theme, trial


def site_theme(request):
    """
    `site_theme` — 'classic' or 'print' — for base.html and the includes
    to switch on, and `site_theme_previewing` for the staff preview banner.

    Lazy, so admin pages and other templates that never look at the theme
    don't pay for the settings lookup.
    """
    return {
        'site_theme': SimpleLazyObject(lambda: theme.get_theme(request)),
        'site_theme_previewing': SimpleLazyObject(lambda: theme.is_previewing(request)),
        'theme_preview_param': theme.PREVIEW_PARAM,
    }


def trial_mode(request):
    """
    `trial_mode` for the trial banner (includes/trial_banner.html): empty
    while trial mode is off.  Lazy, like the theme above.
    """
    return {'trial_mode': SimpleLazyObject(lambda: trial.banner(request))}
