from django import template
from ..models import SiteFooter  # replace your_app with your actual app name

register = template.Library()


@register.simple_tag
def get_site_footer():
    """
    Returns the first SiteFooter snippet, or None if none exists yet.

    Usage in templates:
        {% load footer_tags %}
        {% get_site_footer as footer %}
    """
    return SiteFooter.objects.prefetch_related("links").first()