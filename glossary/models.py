"""
The science glossary.

A GlossaryTerm is one word and what it means.  An editor marks a word in an
article with the "Glossary term" button of the rich-text toolbar; readers see
it underlined, and a small card explains it when they point at it, reach it
with the keyboard or tap it.

    glossary/rich_text.py            how a marked word is stored and rendered
    glossary/static/js/glossary.js   the card
    glossary/views.py                the /glossary/ pages and the card's data

Names and explanations exist per language, the way an article's bodies do:
the Malayalam ones have no suffix, the others end in _en, _hi and _ta.  Only
the Malayalam name and one explanation are required, and most terms will be
explained in Malayalam or English alone.  A reader therefore gets the
explanation in the language they are reading when there is one, and the
first one there is (in the order below) when there is not.
"""
import secrets

from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils.html import strip_tags
from django.utils.safestring import mark_safe
from django.utils.text import slugify
from wagtail.admin.panels import FieldPanel, MultiFieldPanel
from wagtail.fields import RichTextField
from wagtail.rich_text import expand_db_html
from wagtail.search import index

#: Language code and its own name, in the order the article page offers them.
TERM_LANGUAGES = (
    ('ml', 'മലയാളം'),
    ('en', 'English'),
    ('hi', 'हिन्दी'),
    ('ta', 'தமிழ்'),
)

#: An explanation is a sentence or two: emphasis and notation (CO₂, 10⁻⁹) are
#: all it needs.  No links or images — the card is small, and on import a term
#: arrives before any page a link could point at.
DEFINITION_FEATURES = ['bold', 'italic', 'superscript', 'subscript']


def _field(base, code):
    return base if code == 'ml' else f'{base}_{code}'


class GlossaryTerm(index.Indexed, models.Model):
    term = models.CharField("Term (Malayalam)", max_length=150)
    term_en = models.CharField("Term (English)", max_length=150, blank=True)
    term_hi = models.CharField("Term (Hindi)", max_length=150, blank=True)
    term_ta = models.CharField("Term (Tamil)", max_length=150, blank=True)

    definition = RichTextField(
        "Explanation (Malayalam)", blank=True, features=DEFINITION_FEATURES)
    definition_en = RichTextField(
        "Explanation (English)", blank=True, features=DEFINITION_FEATURES)
    definition_hi = RichTextField(
        "Explanation (Hindi)", blank=True, features=DEFINITION_FEATURES)
    definition_ta = RichTextField(
        "Explanation (Tamil)", blank=True, features=DEFINITION_FEATURES)

    slug = models.SlugField(
        "Page address", max_length=160, unique=True, blank=True,
        help_text=(
            "The last part of this term's own page: /glossary/photosynthesis/. "
            "Leave it empty and it is made from the English term. Changing it "
            "later breaks links to that page from other sites."
        ),
    )

    panels = [
        MultiFieldPanel(
            [FieldPanel('term'), FieldPanel('definition')], heading="Malayalam"),
        MultiFieldPanel(
            [FieldPanel('term_en'), FieldPanel('definition_en')], heading="English"),
        MultiFieldPanel(
            [FieldPanel('term_hi'), FieldPanel('definition_hi')],
            heading="Hindi", classname="collapsed"),
        MultiFieldPanel(
            [FieldPanel('term_ta'), FieldPanel('definition_ta')],
            heading="Tamil", classname="collapsed"),
        FieldPanel('slug'),
    ]

    search_fields = [
        index.SearchField('term'),
        index.AutocompleteField('term'),
        index.SearchField('term_en'),
        index.AutocompleteField('term_en'),
        index.SearchField('term_hi'),
        index.SearchField('term_ta'),
    ]

    class Meta:
        ordering = ['term']
        verbose_name = "Glossary term"

    def __str__(self):
        return f'{self.term} ({self.term_en})' if self.term_en else self.term

    # ── Per language ────────────────────────────────────────────────────

    def _filled(self, base):
        out = {}
        for code, _name in TERM_LANGUAGES:
            value = getattr(self, _field(base, code))
            # An emptied rich-text box can still hold an empty paragraph.
            if value and strip_tags(value).strip():
                out[code] = value
        return out

    @property
    def names(self):
        """{language code: the term in that language}, for those filled in."""
        return self._filled('term')

    @property
    def explanations(self):
        """{language code: the explanation as HTML}, for those filled in."""
        # Editors' rich text, safe to output as it is — like |richtext.
        return {
            code: mark_safe(expand_db_html(html))
            for code, html in self._filled('definition').items()
        }

    def explanation_for(self, lang):
        """
        (language code, HTML) of the explanation for a reader of `lang`: in
        that language if there is one, else the first there is.
        """
        text = self.explanations
        for code in [lang] + [c for c, _name in TERM_LANGUAGES]:
            if code in text:
                return code, text[code]
        return None, ''

    def explained_in(self):
        """The languages this term is explained in (a column of the admin list)."""
        own_names = dict(TERM_LANGUAGES)
        return ', '.join(own_names[code] for code in self._filled('definition'))
    explained_in.short_description = "Explained in"

    def as_card(self):
        """What the reader's card shows (see static/js/glossary.js)."""
        return {
            'id': self.pk,
            'url': self.get_absolute_url(),
            'names': self.names,
            'text': self.explanations,
        }

    # ── Saving ──────────────────────────────────────────────────────────

    def _free_slug(self):
        base = slugify(self.term_en)[:150] or f'term-{secrets.token_hex(3)}'
        slug, n = base, 2
        while GlossaryTerm.objects.filter(slug=slug).exclude(pk=self.pk).exists():
            slug, n = f'{base}-{n}', n + 1
        return slug

    def clean(self):
        super().clean()
        if not self._filled('definition'):
            raise ValidationError(
                {'definition': "Explain the term in at least one language."})
        if not self.slug:
            self.slug = self._free_slug()

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._free_slug()
        super().save(*args, **kwargs)

    # ── Where it is used ────────────────────────────────────────────────

    def get_absolute_url(self):
        return reverse('glossary_term', kwargs={'slug': self.slug})

    def articles(self):
        """Published articles that mark a word with this term, newest first."""
        from django.contrib.contenttypes.models import ContentType
        from wagtail.models import ReferenceIndex

        from articles.models import Article

        ids = ReferenceIndex.get_references_to(self).filter(
            content_type=ContentType.objects.get_for_model(Article),
        ).values_list('object_id', flat=True)
        return (
            Article.objects.live().public()
            .filter(pk__in={int(pk) for pk in ids})
            .order_by('-date')
        )
