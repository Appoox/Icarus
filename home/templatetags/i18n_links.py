"""
{% load i18n_links %}{% language_alternates as alternates %}

The same page in every site language (Malayalam unprefixed, English under
/en/), for <link rel="alternate" hreflang> tags. Built with Django's
translate_url, so it follows i18n_patterns exactly.
"""
from django import template
from django.conf import settings
from django.urls import translate_url

register = template.Library()


@register.simple_tag(takes_context=True)
def language_alternates(context):
    request = context.get("request")
    if request is None:
        return []
    current = request.build_absolute_uri()
    return [(code, translate_url(current, code)) for code, _name in settings.LANGUAGES]
