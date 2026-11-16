"""
A glossary term inside rich text.

    stored     <a linktype="glossary" id="12">പ്രകാശസംശ്ലേഷണത്തിലൂടെ</a>
    rendered   <a class="glossary-term" href="/glossary/photosynthesis/"
                  data-glossary-id="12">പ്രകാശസംശ്ലേഷണത്തിലൂടെ</a>

Only the term's id is stored, the way Wagtail stores page and document links:

  * the marked word can be any form of the term (an inflected one, above),
  * correcting an explanation corrects it in every article at once,
  * Wagtail knows which articles use a term (its "Usage" screen, the list on
    the term's own page, and the warning before a term is deleted).

The explanation itself is never put in the article's HTML.  Whatever strips
tags from a body — text-to-speech, reading time, the search index, excerpts —
therefore still gets the article's own words and nothing else.

Being an id, the reference is remapped by export_content / import_content
(home/management/commands/_richtext.py).
"""
from django.utils.html import escape
from draftjs_exporter.dom import DOM
from wagtail.admin.rich_text.converters.html_to_contentstate import LinkElementHandler
from wagtail.rich_text import LinkHandler

FEATURE_NAME = 'glossary'
LINK_TYPE = 'glossary'
ENTITY_TYPE = 'GLOSSARY'


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ── Database HTML → the page ────────────────────────────────────────────

class GlossaryLinkHandler(LinkHandler):
    identifier = LINK_TYPE

    @staticmethod
    def get_model():
        from .models import GlossaryTerm
        return GlossaryTerm

    @classmethod
    def get_many(cls, attrs_list):
        # One query for every term in a block of text; None for a term that
        # has been deleted since (or an id that is not a number at all).
        ids = [_as_int(attrs.get('id')) for attrs in attrs_list]
        terms = cls.get_model().objects.in_bulk([pk for pk in ids if pk is not None])
        return [terms.get(pk) for pk in ids]

    @classmethod
    def expand_db_attributes(cls, attrs):
        return cls.expand_db_attributes_many([attrs])[0]

    @classmethod
    def expand_db_attributes_many(cls, attrs_list):
        return [
            '<a class="glossary-term" href="%s" data-glossary-id="%d">'
            % (escape(term.get_absolute_url()), term.pk)
            if term else '<a>'  # deleted term: the word stays, unmarked
            for term in cls.get_many(attrs_list)
        ]

    @classmethod
    def extract_references(cls, attrs):
        pk = _as_int(attrs.get('id'))
        if pk is not None:
            yield cls.get_model(), str(pk), '', ''


# ── Database HTML ↔ the editor ──────────────────────────────────────────

def glossary_entity_decorator(props):
    """Editor → database HTML."""
    return DOM.create_element(
        'a', {'linktype': LINK_TYPE, 'id': props.get('id')}, props['children'])


class GlossaryElementHandler(LinkElementHandler):
    """Database HTML → editor, with the term's name for the editor's tooltip."""

    def get_attribute_data(self, attrs):
        pk = _as_int(attrs.get('id'))
        if pk is None:
            return {}
        term = GlossaryLinkHandler.get_model().objects.filter(pk=pk).first()
        if term is None:
            return {'id': pk}  # kept, and shown as a missing term
        return {'id': pk, 'string': str(term)}


ContentstateGlossaryConversionRule = {
    'from_database_format': {
        f'a[linktype="{LINK_TYPE}"]': GlossaryElementHandler(ENTITY_TYPE),
    },
    'to_database_format': {
        'entity_decorators': {ENTITY_TYPE: glossary_entity_decorator},
    },
}
