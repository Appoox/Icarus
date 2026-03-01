from django import template
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
