"""
The admin side of the glossary: the Glossary screen, and the toolbar button
that marks a word in rich text as a glossary term.
"""
import wagtail.admin.rich_text.editors.draftail.features as draftail_features
from django.urls import reverse_lazy
from wagtail import hooks
from wagtail.snippets.models import register_snippet
from wagtail.snippets.views.snippets import SnippetViewSet

from .models import GlossaryTerm
from .rich_text import (
    ENTITY_TYPE,
    FEATURE_NAME,
    ContentstateGlossaryConversionRule,
    GlossaryLinkHandler,
)


class GlossaryTermViewSet(SnippetViewSet):
    model = GlossaryTerm
    menu_label = "Glossary"
    icon = "openquote"
    menu_order = 160            # after the article lists
    add_to_admin_menu = True

    list_display = ["term", "term_en", "explained_in", "slug"]
    ordering = ["term"]


register_snippet(GlossaryTermViewSet)


@hooks.register('register_rich_text_features')
def register_glossary_feature(features):
    features.register_link_type(GlossaryLinkHandler)

    features.register_editor_plugin(
        'draftail',
        FEATURE_NAME,
        draftail_features.EntityFeature(
            {
                'type': ENTITY_TYPE,
                'icon': 'openquote',
                'description': 'Glossary term',
                # Wagtail's own chooser for the snippet, opened in a modal.
                'chooserUrls': {
                    'glossaryChooser': reverse_lazy(
                        'wagtailsnippetchoosers_glossary_glossaryterm:choose'),
                },
            },
            js=['wagtailadmin/js/chooser-modal.js', 'js/draftail_glossary.js'],
        ),
    )
    features.register_converter_rule(
        'contentstate', FEATURE_NAME, ContentstateGlossaryConversionRule)

    # On in every rich-text box that doesn't list its own features (article
    # paragraphs, callouts, quotes, captions) — as the colour and alignment
    # buttons are.
    features.default_features.append(FEATURE_NAME)
