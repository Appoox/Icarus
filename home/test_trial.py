"""
Tests for trial mode (home/trial.py): readers can't open issues that
aren't picked (or their articles and archive scans), editors see
everything, and everyone sees the trial banner.

    python manage.py test home.test_trial --settings=Icarus.settings.test
"""
from datetime import date
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from home import theme
from home.models import TrialModeSettings
from home.test_theme import SiteTreeMixin
from issue.models import Issue, IssueArticleReprint
from articles.models import Article
from the_librarian.models import ArchiveDocument, ArchiveIssue

User = get_user_model()

DEFAULT_BANNER = 'ഈ വെബ്സൈറ്റ് ഇപ്പോൾ പരീക്ഷണാടിസ്ഥാനത്തിലാണ്.'
DEFAULT_HEADING = 'ഇപ്പോൾ ലഭ്യമല്ല'
DEFAULT_MESSAGE = 'പരീക്ഷണ ഘട്ടത്തിൽ ഈ ഉള്ളടക്കം ലഭ്യമല്ല.'


class TrialModeTests(SiteTreeMixin, TestCase):
    """
    The site from SiteTreeMixin (August 2025 issue with two articles), plus:
    a newer September issue with an article of its own and one reprinted
    in August, an unpublished October issue with a published article, and
    an article with no issue.  Trial mode opens only August.
    """

    def setUp(self):
        # A request to /en/ leaves English active after it; start each test
        # in Malayalam and leave the default behind for the next one.
        translation.activate(settings.LANGUAGE_CODE)
        self.addCleanup(translation.deactivate)
        self.build_site()

        self.other_issue = Issue(
            title='സെപ്റ്റംബർ 2025', slug='sep-2025', date_of_publishing=date(2025, 9, 1),
        )
        self.issue_index.add_child(instance=self.other_issue)
        self.other_article = self.add_article('groundwater', 'ഭൂഗർഭജലം', self.other_issue)
        self.reprinted = self.add_article('reprint', 'പുനഃപ്രസിദ്ധീകരണം', self.other_issue)
        IssueArticleReprint.objects.create(issue=self.issue, article=self.reprinted)

        self.draft_issue = Issue(
            title='ഒക്ടോബർ 2025', slug='oct-2025', date_of_publishing=date(2025, 10, 1), live=False,
        )
        self.issue_index.add_child(instance=self.draft_issue)
        self.early_article = self.add_article('early', 'നേരത്തെ', self.draft_issue)
        self.web_only = self.add_article('web-only', 'വെബ് മാത്രം', None)

        self.trial = TrialModeSettings.for_site(self.site)

    def add_article(self, slug, title, issue):
        article = Article(
            title=title, slug=slug, date=date(2025, 9, 1), main_issue=issue,
            body=[('paragraph', '<p>വാചകം.</p>')],
        )
        self.article_index.add_child(instance=article)
        return article

    def turn_on(self, *issues, **texts):
        self.trial.enabled = True
        for field, value in texts.items():
            setattr(self.trial, field, value)
        self.trial.save()
        self.trial.allowed_issues.set(issues or [self.issue])

    def assertOpens(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, path)
        return response

    def assertUnavailable(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 404, path)
        self.assertContains(response, DEFAULT_HEADING, status_code=404)
        self.assertContains(response, DEFAULT_MESSAGE, status_code=404)
        # The open issues, to read instead.
        self.assertContains(response, self.issue.url, status_code=404)
        return response

    def login(self, **extra):
        user = User.objects.create_user(
            phone_number=f'+9188000{User.objects.count():05d}', name='User', password='x', **extra,
        )
        self.client.force_login(user)
        return user

    def login_subscriber(self):
        user = self.login()
        user.grant_temporary_access(days=30)
        return user

    def scan(self, year, month, **extra):
        return ArchiveIssue.objects.create(year=year, month=month, volume=60, issue_number=month, **extra)

    # ── Off ──────────────────────────────────────────────────────────────

    def test_off_by_default_everything_opens_and_no_banner(self):
        self.assertFalse(self.trial.enabled)
        response = self.assertOpens(self.other_issue.url)
        self.assertOpens(self.other_article.url)
        self.assertNotContains(response, 'trial-banner')

    # ── Issues and articles ──────────────────────────────────────────────

    def test_picked_issue_and_its_articles_open(self):
        self.turn_on()
        self.assertOpens(self.issue.url)
        self.assertOpens(self.feature.url)
        self.assertOpens(self.column.url)

    def test_other_issues_and_their_articles_are_not_available(self):
        self.turn_on()
        self.assertUnavailable(self.other_issue.url)
        self.assertUnavailable(self.other_article.url)
        self.assertUnavailable(self.other_issue.url + 'pdf/')

    def test_reprint_in_a_picked_issue_opens(self):
        self.turn_on()
        self.assertOpens(self.reprinted.url)

    def test_article_with_no_issue_opens(self):
        self.turn_on()
        self.assertOpens(self.web_only.url)

    def test_article_of_an_unpublished_issue_is_not_available(self):
        self.turn_on()
        self.assertUnavailable(self.early_article.url)

    def test_signed_in_reader_is_restricted_too(self):
        self.turn_on()
        self.login()
        self.assertOpens(self.issue.url)
        self.assertUnavailable(self.other_issue.url)

    def test_rest_of_the_site_and_its_lists_stay_open(self):
        self.turn_on()
        self.assertOpens(self.home.url)
        self.assertOpens(self.author.url)
        self.assertOpens(self.article_index.url)
        self.assertOpens(reverse('topic_detail', args=['quantum']))
        self.assertOpens(reverse('account_login'))
        # Lists still show the closed issue and its articles.
        response = self.assertOpens(self.issue_index.url)
        self.assertEqual(set(response.context['issues']), {self.issue, self.other_issue})
        response = self.assertOpens(self.feature.url)
        self.assertIn(self.other_article, set(response.context['related_articles']))

    def test_custom_heading_and_message(self):
        self.turn_on(unavailable_heading='അടച്ചിരിക്കുന്നു', unavailable_message='പിന്നീട് വരൂ.')
        response = self.client.get(self.other_issue.url)
        self.assertContains(response, 'അടച്ചിരിക്കുന്നു', status_code=404)
        self.assertContains(response, 'പിന്നീട് വരൂ.', status_code=404)
        self.assertNotContains(response, DEFAULT_HEADING, status_code=404)

    def test_not_available_page_on_the_english_site(self):
        # Checks the page, not the English wording (see test_banner_on_the_english_site).
        self.turn_on()
        # Literal paths: after a request to /en/, .url would be English too.
        response = self.client.get('/en/issues/sep-2025/')
        self.assertContains(response, '<html lang="en">', status_code=404)
        self.assertContains(response, 'trial-banner', status_code=404)
        # The open issue, at its English address.
        self.assertContains(response, 'href="/en/issues/aug-2025/"', status_code=404)

    def test_not_available_page_in_print_design(self):
        self.turn_on()
        self.use_theme(theme.THEME_PRINT)
        response = self.assertUnavailable(self.other_article.url)
        self.assertContains(response, 'pt-issuegrid', status_code=404)

    def test_no_picked_issues_closes_every_issue(self):
        self.turn_on()
        self.trial.allowed_issues.clear()
        response = self.client.get(self.issue.url)
        self.assertContains(response, DEFAULT_HEADING, status_code=404)
        self.assertOpens(self.web_only.url)

    # ── Archive ──────────────────────────────────────────────────────────

    def test_scans_of_closed_issues_are_not_available(self):
        self.turn_on()
        self.login_subscriber()
        picked = self.scan(2025, 8)
        closed = self.scan(2025, 9)
        self.assertOpens(reverse('the_librarian:magazine_viewer', args=[picked.pk]))
        self.assertUnavailable(reverse('the_librarian:magazine_viewer', args=[closed.pk]))
        self.assertUnavailable(reverse('the_librarian:magazine_page_meta', args=[closed.pk]))
        self.assertUnavailable(reverse('the_librarian:download_magazine_pdf', args=[closed.pk]))

    def test_scans_without_a_published_issue_page_stay_open(self):
        self.turn_on()
        self.login_subscriber()
        self.assertOpens(reverse('the_librarian:magazine_viewer', args=[self.scan(1999, 5).pk]))
        # October's issue page exists but isn't published.
        self.assertOpens(reverse('the_librarian:magazine_viewer', args=[self.scan(2025, 10).pk]))

    def test_closed_scan_is_closed_from_search_results_too(self):
        self.turn_on()
        self.login_subscriber()
        linked = ArchiveDocument.objects.create(filename='sep.pdf', file_path='archive/sep.pdf')
        self.scan(2025, 9, archive_document=linked)
        standalone = ArchiveDocument.objects.create(filename='loose.pdf', file_path='loose.pdf')
        self.assertUnavailable(reverse('the_librarian:viewer', args=[linked.pk]))
        self.assertOpens(reverse('the_librarian:viewer', args=[standalone.pk]))
        self.assertOpens(reverse('the_librarian:archive_list'))

    def test_closed_scan_file_is_refused_to_caddy(self):
        self.turn_on()
        self.login_subscriber()
        folder = Path(settings.MEDIA_ROOT) / 'archive'
        folder.mkdir(parents=True, exist_ok=True)
        for name in ('sep.pdf', 'aug.pdf'):
            (folder / name).write_bytes(b'%PDF-1.4')
        ArchiveIssue.objects.filter(pk=self.scan(2025, 9).pk).update(pdf_file='archive/sep.pdf')
        ArchiveIssue.objects.filter(pk=self.scan(2025, 8).pk).update(pdf_file='archive/aug.pdf')

        def authz(name):
            return self.client.get(
                reverse('archive_authz'), HTTP_X_FORWARDED_URI=f'/media/archive/{name}',
            ).status_code

        self.assertEqual(authz('sep.pdf'), 404)
        self.assertEqual(authz('aug.pdf'), 200)

    # ── Editors ──────────────────────────────────────────────────────────

    def test_staff_see_everything(self):
        self.turn_on()
        self.login(is_staff=True)
        self.assertOpens(self.other_issue.url)
        self.assertOpens(self.other_article.url)
        self.assertOpens(reverse('the_librarian:magazine_viewer', args=[self.scan(2025, 9).pk]))

    def test_admin_users_without_staff_flag_are_editors(self):
        self.turn_on()
        user = self.login()
        group = Group.objects.create(name='Trial editors')
        group.permissions.add(Permission.objects.get(codename='access_admin'))
        user.groups.add(group)
        self.assertOpens(self.other_issue.url)

    # ── Banner ───────────────────────────────────────────────────────────

    def test_banner_for_readers(self):
        self.turn_on()
        response = self.assertOpens(self.issue.url)
        self.assertContains(response, 'trial-banner')
        self.assertContains(response, DEFAULT_BANNER)
        self.assertNotContains(response, 'എഡിറ്ററായതിനാൽ')
        # On the "not available" page too.
        response = self.client.get(self.other_issue.url)
        self.assertContains(response, DEFAULT_BANNER, status_code=404)

    def test_banner_on_the_english_site(self):
        # Checks the page, not the English wording, as the glossary and home
        # tests do: CI doesn't compile the translations (the .mo is built in
        # the Docker image).
        self.turn_on()
        response = self.assertOpens('/en/issues/aug-2025/')
        self.assertContains(response, '<html lang="en">')
        self.assertContains(response, 'trial-banner')

    def test_custom_banner_text(self):
        self.turn_on(banner_message='പരീക്ഷണം <b>നടക്കുന്നു</b>')
        response = self.assertOpens(self.issue.url)
        self.assertContains(response, 'പരീക്ഷണം &lt;b&gt;നടക്കുന്നു&lt;/b&gt;')
        self.assertNotContains(response, DEFAULT_BANNER)

    def test_banner_tells_editors_what_readers_see(self):
        self.turn_on()
        self.client.force_login(
            User.objects.create_superuser(phone_number='+918800990001', name='Root', password='x')
        )
        response = self.assertOpens(self.other_issue.url)
        self.assertContains(response, 'വായനക്കാർക്ക് തിരഞ്ഞെടുത്ത 1 ലക്കം മാത്രമേ കാണാനാകൂ.')
        self.assertContains(
            response, reverse('wagtailsettings:edit', args=['home', 'trialmodesettings', self.site.pk]),
        )

    def test_staff_without_settings_permission_get_no_settings_link(self):
        self.turn_on()
        self.login(is_staff=True)
        response = self.assertOpens(self.issue.url)
        self.assertContains(response, 'എഡിറ്ററായതിനാൽ')
        self.assertNotContains(response, 'trialmodesettings')

    # ── Admin ────────────────────────────────────────────────────────────

    def test_admin_settings_page_saves_everything(self):
        self.client.force_login(
            User.objects.create_superuser(phone_number='+918800990002', name='Root', password='x')
        )
        url = reverse('wagtailsettings:edit', args=['home', 'trialmodesettings', self.site.pk])
        response = self.client.get(url)
        self.assertContains(response, 'സെപ്റ്റംബർ 2025 (2025-09)')
        self.assertContains(response, 'ഒക്ടോബർ 2025 (2025-10) — not published')
        response = self.client.post(url, {
            'enabled': 'on',
            'allowed_issues': [self.issue.pk, self.other_issue.pk],
            'banner_message': '',
            'unavailable_heading': 'തലക്കെട്ട്',
            'unavailable_message': '',
        })
        self.assertEqual(response.status_code, 302)
        saved = TrialModeSettings.for_site(self.site)
        self.assertTrue(saved.enabled)
        self.assertEqual(set(saved.allowed_issues.all()), {self.issue, self.other_issue})
        self.assertEqual(saved.unavailable_heading, 'തലക്കെട്ട്')
