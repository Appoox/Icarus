"""
Tests for the site design switch (home/theme.py) and the print edition
design's templates.

    python manage.py test home.test_theme --settings=Icarus.settings.test
"""
from datetime import date
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase
from django.urls import reverse
from wagtail.models import Page, Site

from articles.models import Article, ArticleIndexPage
from home import theme
from home.models import HomePage, SiteThemeSettings
from home.templatetags.print_theme import initials
from issue.models import Issue, IssueIndexPage, Topic, Volume
from literati.models import ArticleAuthorRelationship, AuthorIndexPage, Literati
from the_librarian.print_archive import _decade_groups

User = get_user_model()


class IssuePaletteTests(TestCase):
    """
    The picked colour is used as-is for fills and decoration; text gets
    a readable shade of it.  The design's own colours come out unchanged.
    """

    paper = theme.parse_hex(theme.PAPER_LIGHT)
    dusk = theme.parse_hex(theme.PAPER_DUSK)

    def test_design_cobalt_is_already_readable(self):
        palette = theme.issue_palette('#1d5fc4')
        self.assertEqual(palette['issue'], '#1d5fc4')
        # The other shades are plain mixes of it.
        self.assertEqual(palette['issue_dark'], theme.to_hex(theme.mix((29, 95, 196), (0, 0, 0), 0.3)))
        self.assertEqual(palette['issue_bright'], theme.to_hex(theme.mix((29, 95, 196), (255, 255, 255), 0.25)))
        self.assertEqual(palette['issue_light'], theme.to_hex(theme.mix((29, 95, 196), (255, 255, 255), 0.6)))

    def test_light_colour_keeps_its_fill_and_gets_readable_text(self):
        palette = theme.issue_palette('#ffd84d')  # yellow: unreadable as text on paper
        # Fills, stripes and covers show the colour as picked…
        self.assertEqual(palette['issue_fill'], '#ffd84d')
        self.assertEqual(palette['issue_bright'], theme.to_hex(theme.mix((255, 216, 77), (255, 255, 255), 0.25)))
        # …with dark text on them, since white would not read on yellow.
        self.assertEqual(palette['on_issue'], theme.INK)
        # Text on the paper is darkened only as far as it has to be:
        # 4.5:1 for small text, 3:1 for headlines and big numerals.
        text = theme.parse_hex(palette['issue'])
        display = theme.parse_hex(palette['issue_display'])
        self.assertGreaterEqual(theme.contrast(text, self.paper), 4.5)
        self.assertGreaterEqual(theme.contrast(display, self.paper), 3.0)
        self.assertGreater(theme.luminance(display), theme.luminance(text))
        # On the black band it is bright enough as it is.
        self.assertEqual(palette['issue_on_band'], '#ffd84d')

    def test_text_on_the_fill_is_whichever_reads_better(self):
        for colour in ('#1d5fc4', '#0e7c83', '#7b3fb0', '#ffd400', '#e53935', '#7ed321', '#ffffff', '#000000'):
            palette = theme.issue_palette(colour)
            fill, on = theme.parse_hex(palette['issue_fill']), theme.parse_hex(palette['on_issue'])
            self.assertEqual(palette['issue_fill'], colour, colour)
            self.assertGreaterEqual(theme.contrast(fill, on), 4.5, colour)

    def test_mid_tone_headlines_keep_the_exact_colour(self):
        # Red reads at 3.8:1 on the paper: enough for large text, not small.
        palette = theme.issue_palette('#e53935')
        self.assertEqual(palette['issue_display'], '#e53935')
        self.assertNotEqual(palette['issue'], '#e53935')

    def test_dark_colour_is_lightened_on_the_band(self):
        palette = theme.issue_palette('#14213d')  # navy: invisible on the black band
        self.assertEqual(palette['issue_fill'], '#14213d')
        band = theme.parse_hex(theme.BAND)
        self.assertGreaterEqual(theme.contrast(theme.parse_hex(palette['issue_on_band']), band), 4.5)

    def test_dark_theme_text_shade_is_readable_on_dark_paper(self):
        for colour in ('#1d5fc4', '#7b3fb0', '#15171c', '#0e7c83', '#ffd400'):
            palette = theme.issue_palette(colour)
            self.assertGreaterEqual(
                theme.contrast(theme.parse_hex(palette['dark_issue']), self.dusk), 4.5, colour,
            )
            self.assertGreaterEqual(
                theme.contrast(theme.parse_hex(palette['dark_issue_display']), self.dusk), 3.0, colour,
            )

    def test_invalid_colour_falls_back(self):
        self.assertEqual(theme.issue_palette('', fallback='#0e7c83')['picked'], '#0e7c83')
        self.assertEqual(theme.issue_palette('not a colour')['picked'], theme.DEFAULT_ISSUE_COLOUR)
        self.assertIsNone(theme.parse_hex('#12345'))

    def test_cover_colours_rotate_without_an_issue_colour(self):
        bg, stripe, on = theme.cover_colours(2020, 5)
        self.assertIn((bg, stripe), theme.COVER_PALETTE)
        self.assertEqual(on, '#ffffff')
        self.assertEqual(theme.cover_colours(2020, 5, '#7b3fb0'), ('#7b3fb0', theme.issue_palette('#7b3fb0')['issue_light'], '#ffffff'))
        self.assertEqual(theme.cover_colours(2020, 5, '#ffd400')[0::2], ('#ffd400', theme.INK))


class ColourInputTests(TestCase):
    """The issue colour box takes a colour however it is written."""

    def test_normalise_colour(self):
        for typed in ('#FC0', 'fc0', ' FFCC00 ', '#ffcc0080', 'rgb(255, 204, 0)', 'rgba(255,204,0,.5)'):
            self.assertEqual(theme.normalise_colour(typed), '#ffcc00', typed)
        self.assertEqual(theme.normalise_colour(''), '')
        self.assertEqual(theme.normalise_colour('  '), '')
        # Not a colour: left as typed for the validator to report.
        self.assertEqual(theme.normalise_colour('gold'), 'gold')
        self.assertEqual(theme.normalise_colour('rgb(300, 0, 0)'), 'rgb(300, 0, 0)')

    def test_widget_saves_the_normalised_value(self):
        from articles.wagtail_widgets import OptionalColourWidget
        widget = OptionalColourWidget()
        self.assertEqual(widget.value_from_datadict({'colour': 'FC0'}, {}, 'colour'), '#ffcc00')
        self.assertEqual(widget.value_from_datadict({'colour': ''}, {}, 'colour'), '')
        html = widget.render('colour', '#FC0')
        self.assertIn('value="#ffcc00"', html)           # the swatch shows it
        self.assertNotIn('pattern=', html)                # the box no longer refuses other formats


class FilterTests(TestCase):

    def test_initials_skip_honorifics_and_spelled_initials(self):
        self.assertEqual(initials('ഡോ. എൻ ഷാജി'), 'ഷാ')
        self.assertEqual(initials('ഡോ. ടി എസ് പ്രീത'), 'പ്രീ')
        self.assertEqual(initials('അനിൽ കുമാർ'), 'അ')
        self.assertEqual(initials('ടി എസ് രവി'), 'ര')
        self.assertEqual(initials('Dr. John Watson'), 'J')
        self.assertEqual(initials(''), '')

    def test_month_year_follows_the_language(self):
        self.assertEqual(theme.month_year(date(2025, 8, 1), 'ml'), 'ആഗസ്റ്റ് 2025')
        self.assertEqual(theme.month_year(date(2025, 8, 1), 'en'), 'August 2025')
        self.assertEqual(theme.next_month(date(2025, 12, 1)), (2026, 1))

    def test_archive_decade_labels(self):
        years = [2015, 2014, 2010, 2009, 2000, 1999, 1969, 1966]
        labels = [g['label'] for g in _decade_groups(years)]
        self.assertEqual(labels, ['2010–15', '2000കൾ', '1999', '1966–69'])


class SiteTreeMixin:
    """A small site: home, issue, two articles, an author."""

    def build_site(self):
        Site.objects.all().delete()
        for p in Page.objects.filter(depth__gt=1):
            if p.depth == 2:
                p.delete()
        root = Page.get_first_root_node()
        self.home = HomePage(title='Home', slug='home')
        root.add_child(instance=self.home)
        self.site = Site.objects.create(hostname='testserver', root_page=self.home, is_default_site=True)

        self.issue_index = IssueIndexPage(title='ലക്കങ്ങൾ', slug='issues')
        self.home.add_child(instance=self.issue_index)
        self.article_index = ArticleIndexPage(title='ലേഖനങ്ങൾ', slug='articles')
        self.home.add_child(instance=self.article_index)
        self.author_index = AuthorIndexPage(title='ലേഖകർ', slug='authors')
        self.home.add_child(instance=self.author_index)

        self.theme_topic = Topic.objects.create(name='ക്വാണ്ടം ഭൗതികം', slug='quantum')
        self.column_topic = Topic.objects.create(name='പംക്തികൾ', slug='columns')
        volume = Volume.objects.create(number=60, year_start=2025)
        self.issue = Issue(
            title='ആഗസ്റ്റ് 2025', slug='aug-2025', volume=volume, issue_number=1,
            date_of_publishing=date(2025, 8, 1), topic=self.theme_topic,
            colour='#0e7c83', standfirst='ക്വാണ്ടം ബലതന്ത്രത്തിന് നൂറു വയസ്സ്.',
            page_count=60, price=30, next_issue_theme='ഭൂഗർഭജലം',
            editorial=[('paragraph', '<p>മുഖക്കുറിപ്പിന്റെ വാചകം.</p>')],
        )
        self.issue_index.add_child(instance=self.issue)

        self.author = Literati(title='ഡോ. എൻ ഷാജി', slug='shaji', phone_number='+919876543210')
        self.author_index.add_child(instance=self.author)

        self.feature = Article(
            title='ക്വാണ്ടം മെക്കാനിക്സിന്റെ ചരിത്രം', slug='history', date=date(2025, 8, 1),
            main_issue=self.issue, topic=self.theme_topic,
            print_page_start=15, print_page_end=20, standfirst='നൂറു വർഷം മുൻപ്.',
            body=[
                ('paragraph', '<p>ആദ്യ ഖണ്ഡിക.</p>'),
                ('heading', '<p>ഉപശീർഷകം</p>'),
                ('callout', {'style': 'info', 'title': 'നാഴികക്കല്ലുകൾ', 'text': '<p><b>1900</b> പ്ലാങ്ക്</p>'}),
                ('paragraph', '<p>അവസാന ഖണ്ഡിക.</p>'),
            ],
        )
        self.article_index.add_child(instance=self.feature)
        ArticleAuthorRelationship.objects.create(page=self.feature, author=self.author)

        self.column = Article(
            title='ഹരണഫലം', slug='cartoon', date=date(2025, 8, 1), main_issue=self.issue,
            topic=self.column_topic, kicker='കാർട്ടൂൺപംക്തി', print_page_start=58,
            body=[('paragraph', '<p>കാർട്ടൂൺ.</p>')],
        )
        self.article_index.add_child(instance=self.column)

    def use_theme(self, value):
        settings_obj = SiteThemeSettings.for_site(self.site)
        settings_obj.theme = value
        settings_obj.save()


class ThemeSwitchTests(SiteTreeMixin, TestCase):

    def setUp(self):
        self.build_site()
        self.factory = RequestFactory()

    def request(self, path='/', user=None):
        request = self.factory.get(path)
        SessionMiddleware(lambda r: None).process_request(request)
        request.user = user or AnonymousUser()
        return request

    def test_classic_is_the_default(self):
        self.assertEqual(theme.get_theme(self.request()), theme.THEME_CLASSIC)

    def test_admin_choice_applies_to_everyone(self):
        self.use_theme(theme.THEME_PRINT)
        self.assertEqual(theme.get_theme(self.request()), theme.THEME_PRINT)

    def test_staff_can_preview_without_switching_the_site(self):
        staff = User.objects.create_user(phone_number='+918800000001', name='Staff', password='x', is_staff=True)
        request = self.request('/?theme_preview=print', user=staff)
        self.assertEqual(theme.get_theme(request), theme.THEME_PRINT)
        self.assertTrue(theme.is_previewing(request))

        # The preview sticks for the rest of the session…
        follow_up = self.request('/', user=staff)
        follow_up.session = request.session
        self.assertEqual(theme.get_theme(follow_up), theme.THEME_PRINT)

        # …until it is turned off.
        stop = self.request('/?theme_preview=off', user=staff)
        stop.session = request.session
        self.assertEqual(theme.get_theme(stop), theme.THEME_CLASSIC)

    def test_readers_cannot_preview(self):
        request = self.request('/?theme_preview=print')
        self.assertEqual(theme.get_theme(request), theme.THEME_CLASSIC)
        self.assertFalse(theme.is_previewing(request))

    def test_themed_template_falls_back_to_classic(self):
        self.use_theme(theme.THEME_PRINT)
        names = theme.themed_template(self.request(), 'issue/issue_index_page.html')
        self.assertEqual(names, ['themes/print/issue/issue_index_page.html', 'issue/issue_index_page.html'])

    def test_classic_pages_use_classic_templates(self):
        for url, template in (
            ('/', 'home/home_page.html'),
            (self.feature.url, 'articles/article.html'),
            (self.issue.url, 'issue/issue.html'),
            (self.author.url, 'literati/literati.html'),
            (reverse('the_librarian:archive_list'), 'the_librarian/archive_list.html'),
        ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertTemplateUsed(response, template)
            self.assertTemplateNotUsed(response, 'themes/print/includes/header.html')


class PrintThemePageTests(SiteTreeMixin, TestCase):

    def setUp(self):
        self.build_site()
        self.use_theme(theme.THEME_PRINT)

    def test_home(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'themes/print/home/home_page.html')
        self.assertTemplateUsed(response, 'themes/print/includes/header.html')
        content = response.content.decode()
        self.assertIn('Baloo+Chettan+2', content)
        self.assertIn('ക്വാണ്ടം ഭൗതികം', content)           # the cover theme
        self.assertIn('ഡോ. എൻ ഷാജി', content)               # cover-story author
        self.assertIn('ക്വാണ്ടം മെക്കാനിക്സിന്റെ ചരിത്രം', content)  # in the contents
        self.assertNotIn('ഹരണഫലം', content.split('pt-toc')[1].split('</ol>')[0])  # columns stay out
        # The current issue's colour, darkened just enough to read on paper.
        self.assertIn('--issue:%s;' % theme.issue_palette('#0e7c83')['issue'], content)

    def test_article(self):
        response = self.client.get(self.feature.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'themes/print/articles/article.html')
        content = response.content.decode()
        self.assertIn('pt-article--single', content)
        self.assertIn('നൂറു വർഷം മുൻപ്.', content)          # standfirst
        self.assertIn('പേജ് 15–20', content)                  # in print
        self.assertIn('pt-article__rail', content)
        self.assertEqual(content.count('നാഴികക്കല്ലുകൾ'), 1)  # callout moved to the rail, once
        self.assertIn('ഹരണഫലം', content)                      # next in this issue
        self.assertIn('name="article-id"', content)          # read tracking still wired
        self.assertIn('id="comments-wrapper"', content)

    def test_article_pdf_links_for_readers_with_access(self):
        content = self.client.get(self.feature.url).content.decode()
        self.assertNotIn('article-pdf-download-btn', content)

        staff = User.objects.create_user(phone_number='+918800000004', name='Staff', password='x', is_staff=True)
        self.client.force_login(staff)
        content = self.client.get(self.feature.url).content.decode()
        # One by the share icons, one at the end of the article.
        self.assertEqual(content.count('article-pdf-download-btn'), 2)
        self.assertIn('href="%spdf/"' % self.feature.url, content)

    def test_article_in_print_columns_keeps_callout_in_the_text(self):
        self.feature.print_layout = 'columns'
        self.feature.save()
        content = self.client.get(self.feature.url).content.decode()
        self.assertIn('pt-article--columns', content)
        self.assertNotIn('pt-article__rail', content)
        self.assertEqual(content.count('നാഴികക്കല്ലുകൾ'), 1)

    def test_paywalled_article_shows_the_subscribe_box(self):
        session = self.client.session
        session['free_reads'] = [999, 998, 997]
        session.save()
        content = self.client.get(self.feature.url).content.decode()
        self.assertIn('paywall-wrapper', content)
        self.assertIn('ബാക്കി വായിക്കാൻ വരിക്കാരാകൂ', content)
        self.assertNotIn('അവസാന ഖണ്ഡിക.', content)            # only the preview

    def test_issue(self):
        response = self.client.get(self.issue.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'themes/print/issue/issue.html')
        content = response.content.decode()
        self.assertIn('മുഖ്യവിഷയം', content)                  # theme group first
        self.assertLess(content.index('history/'), content.index('cartoon/'))
        self.assertIn('കാർട്ടൂൺപംക്തി', content)              # column name
        self.assertIn('ഭൂഗർഭജലം', content)                    # next issue
        self.assertIn('സെപ്റ്റംബർ 2025', content)
        self.assertIn('മുഖക്കുറിപ്പ്', content)                # default editorial heading

    def test_light_issue_colour_is_used_as_picked(self):
        self.issue.colour = '#ffd400'
        self.issue.next_issue_colour = '#14213d'
        self.issue.save()
        content = self.client.get(self.issue.url).content.decode()
        style = content.split('<style id="pt-issue-colour">')[1].split('</style>')[0]
        palette = theme.issue_palette('#ffd400')
        self.assertIn('--issue-fill:#ffd400;', style)            # tags, stripe base, panels
        self.assertIn('--on-issue:%s;' % theme.INK, style)       # dark text on the yellow
        self.assertIn('--issue:%s;' % palette['issue'], style)   # readable small text
        self.assertIn('--issue-display:%s;' % palette['issue_display'], style)
        # The next-issue band wears its own colour, with white text on navy.
        self.assertIn('pt-nextissue pt-own-colour" style="--issue-fill:#14213d;--on-issue:#ffffff;', content)

    def test_issue_contents_by_page(self):
        self.issue.contents_order = 'by_page'
        self.issue.save()
        groups = Issue.objects.get(pk=self.issue.pk).contents_groups
        self.assertEqual(len(groups), 1)
        self.assertEqual([a.print_page_start for a in groups[0]['items']], [15, 58])

    def test_author(self):
        response = self.client.get(self.author.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'themes/print/literati/literati.html')
        content = response.content.decode()
        self.assertIn('ഏറ്റവും പുതിയത്', content)
        self.assertIn('2025', content)

    def test_archive(self):
        response = self.client.get(reverse('the_librarian:archive_list'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'themes/print/the_librarian/archive_list.html')
        self.assertEqual(response.context['selected_year'], 2025)
        self.assertEqual(response.context['issues'][0]['theme'], 'ക്വാണ്ടം ഭൗതികം')

    def test_page_without_a_print_template_keeps_its_own(self):
        # Sign-in has no print template: its Classic form is drawn inside
        # the print frame and restyled by print/forms.css.
        response = self.client.get(reverse('account_login'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'account/login.html')
        self.assertTemplateUsed(response, 'themes/print/includes/header.html')
        self.assertContains(response, 'css/print/forms.css')
        self.assertContains(response, 'class="auth-form"')

    def test_list_pages(self):
        for page, template, expect in (
            (self.issue_index, 'themes/print/issue/issue_index_page.html', 'pt-issuecard'),
            (self.article_index, 'themes/print/articles/article_index_page.html', 'ക്വാണ്ടം മെക്കാനിക്സിന്റെ ചരിത്രം'),
            (self.author_index, 'themes/print/literati/author_index_page.html', 'ഡോ. എൻ ഷാജി'),
        ):
            response = self.client.get(page.url)
            self.assertEqual(response.status_code, 200, page.url)
            self.assertTemplateUsed(response, template)
            self.assertContains(response, expect)

    def test_topic_tag_and_most_read_pages(self):
        for url, template in (
            (reverse('topic_detail', args=['quantum']), 'themes/print/issue/topic_detail.html'),
            (reverse('most_read_topics'), 'themes/print/issue/most_read_topics.html'),
            (reverse('most_read_authors'), 'themes/print/literati/most_read_authors.html'),
            (reverse('tag_detail', args=['nothing-tagged']), 'themes/print/articles/tag_detail.html'),
        ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertTemplateUsed(response, template)
        # A topic page lists its articles, and the section nav marks
        # topics (its address sits under /issues/, but topics is longer).
        content = self.client.get(reverse('topic_detail', args=['quantum'])).content.decode()
        self.assertIn('ക്വാണ്ടം മെക്കാനിക്സിന്റെ ചരിത്രം', content)
        active = content.split('aria-current="true">')[1].split('<')[0]
        self.assertEqual(active, 'വിഷയങ്ങൾ')

    def test_search_and_letters(self):
        response = self.client.get(reverse('the_librarian:search'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'themes/print/the_librarian/search.html')

        reader = User.objects.create_user(phone_number='+918800000002', name='Reader', password='x')
        self.client.force_login(reader)
        response = self.client.get(reverse('postbox:page'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'themes/print/postbox/postbox_page.html')
        # postbox.js still finds its form, stars and counter.
        for hook in ('id="fb-form"', 'id="fb-star-label"', 'id="fb-char-count"', 'fb-type-btn'):
            self.assertContains(response, hook)

    def test_notice_pages(self):
        response = self.client.get(reverse('guardian_account_info'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'themes/print/account/guardian_signup.html')
        self.assertContains(response, 'pt-notice-page')

        response = self.client.get(reverse('under_construction'))
        self.assertTemplateUsed(response, 'themes/print/under_construction.html')


class ErrorPageTests(SiteTreeMixin, TestCase):
    """Django's 403/404/500 handlers follow the site design (tests run with DEBUG off)."""

    def setUp(self):
        self.build_site()

    def test_classic_404_is_the_plain_page(self):
        response = self.client.get('/no-such-page/')
        self.assertEqual(response.status_code, 404)
        self.assertTemplateUsed(response, '404.html')
        self.assertNotContains(response, 'pt-notice-page', status_code=404)

    def test_print_404_and_403(self):
        self.use_theme(theme.THEME_PRINT)
        response = self.client.get('/no-such-page/')
        self.assertEqual(response.status_code, 404)
        self.assertTemplateUsed(response, 'themes/print/404.html')
        self.assertContains(response, 'pt-notice-page', status_code=404)
        self.assertContains(response, 'class="pt-header"', status_code=404)  # the site frame

        reader = User.objects.create_user(phone_number='+918800000003', name='Reader', password='x')
        self.client.force_login(reader)
        response = self.client.get(reverse('the_librarian:remote_worker_admin'))
        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, 'themes/print/403.html')

    def test_print_404_falls_back_to_classic_if_the_frame_fails(self):
        self.use_theme(theme.THEME_PRINT)
        with mock.patch('home.templatetags.print_theme.current_issue_for', side_effect=RuntimeError('db')), \
                self.assertLogs('Icarus.views', level='ERROR'):
            response = self.client.get('/no-such-page/')
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, 'pt-notice-page', status_code=404)
        self.assertContains(response, 'class="card"', status_code=404)

    def test_500_follows_the_design_and_survives_a_broken_lookup(self):
        from Icarus.views import server_error
        request = RequestFactory().get('/')
        request.user = AnonymousUser()

        self.assertNotIn(b'class="code"', server_error(request).content)

        self.use_theme(theme.THEME_PRINT)
        request = RequestFactory().get('/')
        request.user = AnonymousUser()
        response = server_error(request)
        self.assertEqual(response.status_code, 500)
        self.assertIn(b'class="code"', response.content)

        with mock.patch('Icarus.views.themed_template', side_effect=RuntimeError('db')):
            response = server_error(RequestFactory().get('/'))
        self.assertEqual(response.status_code, 500)
        self.assertNotIn(b'class="code"', response.content)


class ThemeAdminTests(SiteTreeMixin, TestCase):

    def setUp(self):
        self.build_site()
        self.client.force_login(
            User.objects.create_superuser(phone_number='+918800990001', name='Root', password='x')
        )

    def test_setting_and_issue_editor_render(self):
        settings_url = reverse(
            'wagtailsettings:edit', args=['home', 'sitethemesettings', self.site.pk],
        )
        response = self.client.get(settings_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Print edition')

        response = self.client.get(reverse('wagtailadmin_pages:edit', args=[self.issue.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'optional-colour__swatch')
        self.assertContains(response, 'Print edition design')
