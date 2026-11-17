"""
Tests for the glossary: terms, a marked word in rich text, the reader's pages
and card data, and the admin.

    python manage.py test glossary --settings=Icarus.settings.test
"""
import json

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import translation
from django.utils.html import strip_tags
from wagtail.admin.rich_text.converters.contentstate import ContentstateConverter
from wagtail.rich_text import RichText, expand_db_html
from wagtail.rich_text import features as rich_text_features

from home import theme
from home.test_theme import SiteTreeMixin

from .models import DEFINITION_FEATURES, GlossaryTerm

User = get_user_model()

ML = '<p>സസ്യങ്ങൾ പ്രകാശം ഉപയോഗിച്ച് CO<sub>2</sub>-ൽ നിന്ന് ആഹാരം നിർമ്മിക്കുന്ന പ്രക്രിയ.</p>'
EN = '<p>How plants make food from light.</p>'


def make_term(**fields):
    values = {
        'term': 'പ്രകാശസംശ്ലേഷണം', 'term_en': 'Photosynthesis',
        'definition': ML, 'definition_en': EN,
    }
    values.update(fields)
    return GlossaryTerm.objects.create(**values)


def marked(term, word='പ്രകാശസംശ്ലേഷണത്തിലൂടെ'):
    """A word marked with `term`, as the editor stores it."""
    return f'<a linktype="glossary" id="{term.pk}">{word}</a>'


class GlossaryTestCase(TestCase):
    """
    Starts every test in Malayalam, the site's default.  A request to /en/…
    leaves English active for whatever runs next on the thread, and a term's
    address built outside a request would then start with /en/.
    """

    def setUp(self):
        super().setUp()
        translation.activate('ml')
        self.addCleanup(translation.deactivate)


class TermTests(GlossaryTestCase):

    def test_the_address_comes_from_the_english_term_and_stays_unique(self):
        first = make_term()
        second = make_term(term='പ്രകാശസംശ്ലേഷണം (സസ്യശാസ്ത്രം)')
        self.assertEqual(first.slug, 'photosynthesis')
        self.assertEqual(second.slug, 'photosynthesis-2')
        self.assertEqual(first.get_absolute_url(), '/glossary/photosynthesis/')
        self.assertEqual(str(first), 'പ്രകാശസംശ്ലേഷണം (Photosynthesis)')

        # Renaming the term later leaves its address alone.
        first.term_en = 'Photo-synthesis'
        first.full_clean()
        first.save()
        self.assertEqual(first.slug, 'photosynthesis')

    def test_a_term_with_no_english_name_still_gets_an_address(self):
        term = make_term(term='കോശം', term_en='', definition_en='')
        self.assertRegex(term.slug, r'^term-[0-9a-f]{6}$')
        self.assertEqual(str(term), 'കോശം')

    def test_the_form_fills_in_an_empty_address(self):
        term = GlossaryTerm(term='കോശം', term_en='Cell', definition=ML)
        term.full_clean()
        self.assertEqual(term.slug, 'cell')

    def test_needs_an_explanation_in_some_language(self):
        for empty in ('', '<p></p>', '<p> </p>'):
            term = GlossaryTerm(term='കോശം', term_en='Cell', definition=empty)
            with self.assertRaises(ValidationError) as caught:
                term.full_clean()
            self.assertIn('definition', caught.exception.message_dict)
        # Any one language will do: explanations are mostly Malayalam or English.
        GlossaryTerm(term='കോശം', term_en='Cell', definition_en=EN).full_clean()

    def test_only_the_languages_filled_in_are_offered(self):
        term = make_term(term_ta='ஒளிச்சேர்க்கை', definition_hi='<p> </p>')
        self.assertEqual(list(term.names), ['ml', 'en', 'ta'])
        self.assertEqual(list(term.explanations), ['ml', 'en'])
        self.assertEqual(term.explained_in(), 'മലയാളം, English')

    def test_a_reader_gets_their_language_or_the_first_there_is(self):
        both = make_term()
        self.assertEqual(both.explanation_for('en'), ('en', EN))
        self.assertEqual(both.explanation_for('hi')[0], 'ml')   # no Hindi: Malayalam
        english_only = make_term(term='കോശം', term_en='Cell', definition='')
        self.assertEqual(english_only.explanation_for('ml'), ('en', EN))

    def test_card_data(self):
        term = make_term()
        self.assertEqual(term.as_card(), {
            'id': term.pk,
            'url': '/glossary/photosynthesis/',
            'names': {'ml': 'പ്രകാശസംശ്ലേഷണം', 'en': 'Photosynthesis'},
            'text': {'ml': ML, 'en': EN},
        })


class RichTextTests(GlossaryTestCase):

    def test_a_marked_word_becomes_a_link_to_its_term(self):
        term = make_term()
        self.assertEqual(
            expand_db_html(f'<p>ഇലകൾ {marked(term)} വളരുന്നു.</p>'),
            f'<p>ഇലകൾ <a class="glossary-term" href="/glossary/photosynthesis/" '
            f'data-glossary-id="{term.pk}">പ്രകാശസംശ്ലേഷണത്തിലൂടെ</a> വളരുന്നു.</p>',
        )

    def test_a_missing_term_leaves_the_word_unmarked(self):
        term = make_term()
        gone = term.pk
        term.delete()
        for attrs in (f'id="{gone}"', 'id="0"', 'id="abc"', 'id=""', ''):
            html = expand_db_html(f'<p><a linktype="glossary" {attrs}>വാക്ക്</a></p>')
            self.assertEqual(html, '<p><a>വാക്ക്</a></p>', attrs)

    def test_every_term_in_a_block_comes_from_one_query(self):
        terms = [make_term(term_en=f'Term {n}') for n in range(3)]
        html = '<p>' + ' '.join(marked(term) for term in terms) + '</p>'
        with self.assertNumQueries(1):
            out = expand_db_html(html)
        self.assertEqual(out.count('class="glossary-term"'), 3)

    def test_the_explanation_stays_out_of_the_text(self):
        # Text-to-speech, reading time, excerpts and the search index all
        # strip the tags from a body: they must get the article's own words.
        term = make_term()
        body = RichText(f'<p>ഇലകൾ {marked(term)} വളരുന്നു.</p>')
        self.assertEqual(strip_tags(str(body)).strip(), 'ഇലകൾ പ്രകാശസംശ്ലേഷണത്തിലൂടെ വളരുന്നു.')

    def test_the_mark_survives_the_editor(self):
        term = make_term()
        stored = f'<p data-block-key="a1">ഇലകൾ {marked(term)}</p>'
        converter = ContentstateConverter(features=['glossary'])
        state = json.loads(converter.from_database_format(stored))
        (entity,) = state['entityMap'].values()
        self.assertEqual(entity['type'], 'GLOSSARY')
        self.assertEqual(entity['mutability'], 'MUTABLE')
        # The name is only for the editor's tooltip; the id is what is saved.
        self.assertEqual(entity['data'], {'id': term.pk, 'string': str(term)})
        self.assertIn(
            f'<a linktype="glossary" id="{term.pk}">പ്രകാശസംശ്ലേഷണത്തിലൂടെ</a>',
            converter.to_database_format(json.dumps(state)),
        )

    def test_a_deleted_term_is_kept_in_the_editor_as_missing(self):
        converter = ContentstateConverter(features=['glossary'])
        state = json.loads(converter.from_database_format(
            '<p data-block-key="a1"><a linktype="glossary" id="987654">വാക്ക്</a></p>'))
        (entity,) = state['entityMap'].values()
        self.assertEqual(entity['data'], {'id': 987654})

    def test_the_button_is_on_by_default_but_not_inside_an_explanation(self):
        self.assertIn('glossary', rich_text_features.get_default_features())
        self.assertNotIn('glossary', DEFINITION_FEATURES)
        self.assertNotIn('link', DEFINITION_FEATURES)


class MarkedArticleTests(SiteTreeMixin, GlossaryTestCase):

    def setUp(self):
        super().setUp()
        self.build_site()
        self.term = make_term()
        self.feature.body = [('paragraph', f'<p>ഇലകൾ {marked(self.term)} വളരുന്നു.</p>')]
        self.feature.save()

    def test_the_term_knows_the_articles_that_use_it(self):
        self.assertEqual(list(self.term.articles()), [self.feature])
        # …published ones only.
        self.feature.live = False
        self.feature.save()
        self.assertEqual(list(self.term.articles()), [])

    def test_the_article_page_marks_the_word_and_loads_the_card(self):
        link = (
            f'<a class="glossary-term" href="/glossary/photosynthesis/" '
            f'data-glossary-id="{self.term.pk}">'
        )
        for name in (theme.THEME_CLASSIC, theme.THEME_PRINT):
            self.use_theme(name)
            content = self.client.get(self.feature.url).content.decode()
            self.assertIn(link, content, name)
            self.assertIn('css/glossary.css', content, name)
            self.assertIn('js/glossary.js', content, name)
            self.assertIn('data-terms-url="/glossary/terms.json"', content, name)
            # The explanation is fetched by the card, not printed in the page.
            self.assertNotIn('How plants make food', content, name)

    def test_the_english_site_links_to_the_english_glossary(self):
        content = self.client.get('/en' + self.feature.url).content.decode()
        self.assertIn('href="/en/glossary/photosynthesis/"', content)
        self.assertIn('data-terms-url="/en/glossary/terms.json"', content)


class PageTests(SiteTreeMixin, GlossaryTestCase):

    def setUp(self):
        super().setUp()
        self.build_site()
        self.term = make_term()
        self.cell = make_term(term='കോശം', term_en='Cell', definition='', definition_en=EN)

    def test_index(self):
        for name, template in (
            (theme.THEME_CLASSIC, 'glossary/index.html'),
            (theme.THEME_PRINT, 'themes/print/glossary/index.html'),
        ):
            self.use_theme(name)
            response = self.client.get('/glossary/')
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, template)
            content = response.content.decode()
            self.assertIn('href="/glossary/photosynthesis/"', content)
            self.assertIn('Photosynthesis', content)
            self.assertIn('CO<sub>2</sub>', content)             # rich text, not escaped
            self.assertIn('<dd lang="en">', content)             # കോശം: English only
        # Malayalam names, in order, each under its first letter.
        self.assertEqual(
            [(letter, [e['name'] for e in entries]) for letter, entries in response.context['groups']],
            [('ക', ['കോശം']), ('പ', ['പ്രകാശസംശ്ലേഷണം'])],
        )

    def test_index_on_the_english_site_goes_by_english_names(self):
        response = self.client.get('/en/glossary/')
        self.assertEqual(
            [(letter, [e['name'] for e in entries]) for letter, entries in response.context['groups']],
            [('C', ['Cell']), ('P', ['Photosynthesis'])],
        )
        content = response.content.decode()
        self.assertIn('href="/en/glossary/cell/"', content)
        self.assertIn('How plants make food from light.', content)

    def test_an_empty_glossary(self):
        GlossaryTerm.objects.all().delete()
        for name in (theme.THEME_CLASSIC, theme.THEME_PRINT):
            self.use_theme(name)
            response = self.client.get('/glossary/')
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'പദാവലിയിൽ ഇതുവരെ പദങ്ങളൊന്നും ചേർത്തിട്ടില്ല.')

    def test_term_page(self):
        self.feature.body = [('paragraph', f'<p>{marked(self.term)}</p>')]
        self.feature.save()
        for name, template in (
            (theme.THEME_CLASSIC, 'glossary/term.html'),
            (theme.THEME_PRINT, 'themes/print/glossary/term.html'),
        ):
            self.use_theme(name)
            response = self.client.get('/glossary/photosynthesis/')
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, template)
            # Every language the term has, the site's own first.
            self.assertEqual(
                [(lang['code'], lang['name']) for lang in response.context['languages']],
                [('ml', 'പ്രകാശസംശ്ലേഷണം'), ('en', 'Photosynthesis')],
            )
            content = response.content.decode()
            self.assertIn('How plants make food from light.', content)
            self.assertIn(self.feature.title, content)            # an article that uses it
            self.assertIn('href="/glossary/"', content)
        self.assertEqual(self.client.get('/glossary/no-such-term/').status_code, 404)

    def test_term_page_on_the_english_site_leads_with_english(self):
        response = self.client.get('/en/glossary/photosynthesis/')
        self.assertEqual(response.context['name'], 'Photosynthesis')
        self.assertEqual(response.context['other_name'], 'പ്രകാശസംശ്ലേഷണം')
        self.assertEqual([lang['code'] for lang in response.context['languages']], ['en', 'ml'])


class CardDataTests(GlossaryTestCase):

    def test_returns_the_terms_asked_for(self):
        term, other = make_term(), make_term(term='കോശം', term_en='Cell')
        make_term(term='ജീൻ', term_en='Gene')
        response = self.client.get(f'/glossary/terms.json?ids={term.pk},{other.pk}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'terms': {
            str(term.pk): term.as_card(), str(other.pk): other.as_card(),
        }})
        # The same for every reader, so browsers may keep it.
        self.assertIn('public', response['Cache-Control'])
        self.assertIn('max-age=300', response['Cache-Control'])

    def test_ignores_what_is_not_an_id(self):
        term = make_term()
        junk = f'x,-1,,1e3,²,{"9" * 30},{term.pk}, {term.pk} ,0'
        response = self.client.get('/glossary/terms.json', {'ids': junk})
        self.assertEqual(list(response.json()['terms']), [str(term.pk)])
        self.assertEqual(self.client.get('/glossary/terms.json').json(), {'terms': {}})

    def test_get_only(self):
        self.assertEqual(self.client.post('/glossary/terms.json').status_code, 405)


class AdminTests(SiteTreeMixin, GlossaryTestCase):

    def setUp(self):
        super().setUp()
        self.build_site()
        self.term = make_term()
        self.client.force_login(
            User.objects.create_superuser(phone_number='+918800770001', name='Root', password='x'))

    def test_glossary_screen(self):
        response = self.client.get(reverse('wagtailsnippets_glossary_glossaryterm:list'))
        self.assertContains(response, 'Photosynthesis')
        self.assertContains(response, 'മലയാളം, English')        # the "Explained in" column
        response = self.client.get(
            reverse('wagtailsnippets_glossary_glossaryterm:edit', args=[self.term.pk]))
        self.assertContains(response, 'Explanation (Tamil)')

    def test_adding_a_term(self):
        response = self.client.post(reverse('wagtailsnippets_glossary_glossaryterm:add'), {
            'term': 'കോശം', 'term_en': 'Cell', 'term_hi': '', 'term_ta': '', 'slug': '',
            'definition': json.dumps({'entityMap': {}, 'blocks': [{
                'key': 'a1', 'type': 'unstyled', 'text': 'ജീവന്റെ അടിസ്ഥാന ഘടകം.',
                'depth': 0, 'inlineStyleRanges': [], 'entityRanges': [],
            }]}),
            'definition_en': 'null', 'definition_hi': 'null', 'definition_ta': 'null',
        })
        self.assertEqual(response.status_code, 302)   # saved, back to the list
        cell = GlossaryTerm.objects.get(slug='cell')
        self.assertEqual(list(cell.explanations), ['ml'])

    def test_the_editor_gets_the_button_and_its_chooser(self):
        chooser = reverse('wagtailsnippetchoosers_glossary_glossaryterm:choose')
        response = self.client.get(reverse('wagtailadmin_pages:edit', args=[self.feature.pk]))
        self.assertContains(response, 'js/draftail_glossary.js')
        self.assertContains(response, 'wagtailadmin/js/chooser-modal.js')
        self.assertContains(response, chooser)
        # The chooser itself lists the terms and can be searched.
        self.assertContains(self.client.get(chooser), 'Photosynthesis')
        results = reverse('wagtailsnippetchoosers_glossary_glossaryterm:choose_results')
        self.assertContains(self.client.get(results, {'q': 'Photo'}), 'Photosynthesis')
        self.assertNotContains(self.client.get(results, {'q': 'Zebra'}), 'Photosynthesis')

    def test_usage_lists_the_article(self):
        self.feature.body = [('paragraph', f'<p>{marked(self.term)}</p>')]
        self.feature.save()
        response = self.client.get(
            reverse('wagtailsnippets_glossary_glossaryterm:usage', args=[self.term.pk]))
        self.assertContains(response, self.feature.title)
