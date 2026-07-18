from django import template
from django.urls import translate_url

from ..models import SiteHeader

register = template.Library()


@register.simple_tag
def get_site_header():
    """
    Returns the first SiteHeader snippet, or None if none exists yet.

    Usage in templates:
        {% load header_tags %}
        {% get_site_header as header %}
    """
    return SiteHeader.objects.first()


@register.simple_tag(takes_context=True)
def alternate_language_url(context, lang_code):
    """
    Current page's URL under the given language prefix.

    Django's translate_url() resolves the path against the active urlconf
    and re-reverses it with the target language forced, so it adds/strips
    the /en/ prefix correctly and preserves the query string.  If the path
    can't be resolved it returns the URL unchanged — the switcher degrades
    to a same-page link rather than erroring.

    Usage in templates (labels intentionally NOT wrapped in translate —
    a language switcher names the target language in its own script):
        {% get_current_language as CURRENT_LANGUAGE %}
        {% if CURRENT_LANGUAGE == "en" %}
            <a href="{% alternate_language_url 'ml' %}">മലയാളം</a>
        {% else %}
            <a href="{% alternate_language_url 'en' %}">English</a>
        {% endif %}
    """
    return translate_url(context["request"].get_full_path(), lang_code)