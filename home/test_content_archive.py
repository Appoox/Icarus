"""
Tests for export_content / import_content.

The pure helpers (_richtext, _streamwalk, _console) are tested on their
own; the commands are tested end to end: build a site, export it, wipe the
database back to a fresh install, import, and check what came back.
"""

import io
import json
import os
import shutil
import tempfile
import warnings
import zipfile
from datetime import date
from unittest import mock

import wagtail
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.management.color import no_style
from django.test import SimpleTestCase, TestCase
from wagtail.documents import get_document_model
from wagtail.images import get_image_model
from wagtail.images.tests.utils import get_test_image_file
from wagtail.models import Page, Site
from wagtail.utils.file import hash_filelike

from home.management.commands._console import Console, human_bytes, human_duration
from home.management.commands._richtext import iter_richtext_refs, rewrite_richtext_refs
from home.management.commands._streamwalk import RefMapper, map_stream


def _quiet_console():
    return Console(io.StringIO(), io.StringIO(), no_style(), verbosity=0, progress=False)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class RichTextRefTests(SimpleTestCase):

    def test_only_the_id_attribute_is_rewritten(self):
        # The old rewriter matched id="…" anywhere in the tag, so an alt text
        # or data-id carrying the same number was rewritten instead of — or
        # as well as — the reference itself.
        html = (
            '<embed alt="the hall, id=\'5\'" data-id="5" embedtype="image" '
            'format="left" id="5"/>'
        )
        out = rewrite_richtext_refs(html, {5: 42}, {})
        self.assertEqual(
            out,
            '<embed alt="the hall, id=\'5\'" data-id="5" embedtype="image" '
            'format="left" id="42"/>',
        )
        self.assertEqual(list(iter_richtext_refs(html)), [('image', 5)])

    def test_page_links_are_remapped_only_when_given_a_page_table(self):
        html = '<p><a linktype="page" id="3">next</a> <a linktype="document" id="7">pdf</a></p>'

        self.assertEqual(list(iter_richtext_refs(html)), [('document', 7)])
        self.assertEqual(list(iter_richtext_refs(html, kinds=('page',))), [('page', 3)])
        self.assertEqual(
            rewrite_richtext_refs(html, {}, {7: 8}),
            '<p><a linktype="page" id="3">next</a> <a linktype="document" id="8">pdf</a></p>',
        )
        self.assertEqual(
            rewrite_richtext_refs(html, {}, {7: 8}, page_map={3: 30}),
            '<p><a linktype="page" id="30">next</a> <a linktype="document" id="8">pdf</a></p>',
        )

    def test_an_unmapped_reference_is_zeroed_rather_than_kept(self):
        html = '<embed embedtype="image" id="5"/>'
        self.assertEqual(rewrite_richtext_refs(html, {}, {}), html)
        self.assertEqual(
            rewrite_richtext_refs(html, {}, {}, drop_unmapped=True),
            '<embed embedtype="image" id="0"/>',
        )

    def test_tables_can_be_callables_and_none_means_leave_alone(self):
        html = '<embed embedtype="image" id="2"/><a linktype="document" id="2">x</a>'
        self.assertEqual(
            rewrite_richtext_refs(html, None, lambda pk: pk * 10),
            '<embed embedtype="image" id="2"/><a linktype="document" id="20">x</a>',
        )

    def test_oembeds_and_plain_links_are_ignored(self):
        html = '<embed embedtype="media" url="https://youtu.be/x"/><a href="/about" id="9">about</a>'
        self.assertEqual(list(iter_richtext_refs(html, kinds=('image', 'document', 'page'))), [])
        self.assertEqual(
            rewrite_richtext_refs(html, {9: 1}, {9: 1}, page_map={9: 1}, drop_unmapped=True),
            html,
        )

    def test_non_html_values_pass_through(self):
        for value in (None, '', 'plain text', 42):
            self.assertEqual(rewrite_richtext_refs(value, {}, {}, drop_unmapped=True), value)
            self.assertEqual(list(iter_richtext_refs(value)), [])


class ConsoleFormatTests(SimpleTestCase):

    def test_human_bytes(self):
        self.assertEqual(human_bytes(None), '0 B')
        self.assertEqual(human_bytes(1023), '1023 B')
        self.assertEqual(human_bytes(1536), '1.5 KB')
        self.assertEqual(human_bytes(1234567), '1.2 MB')
        self.assertEqual(human_bytes(5 * 1024 ** 4), '5.0 TB')

    def test_human_duration(self):
        self.assertEqual(human_duration(-3), '0ms')
        self.assertEqual(human_duration(0.25), '250ms')
        self.assertEqual(human_duration(12.34), '12.3s')
        self.assertEqual(human_duration(93.4), '1m 33s')
        self.assertEqual(human_duration(3725), '1h 02m')


class _Recorder(RefMapper):
    """Shifts each kind of id by a fixed amount so a test can see what was mapped."""

    def __init__(self):
        self.seen = []

    def image(self, pk):
        self.seen.append(('image', pk))
        return pk + 1000

    def document(self, pk):
        self.seen.append(('document', pk))
        return pk + 2000

    def page(self, pk):
        self.seen.append(('page', pk))
        return pk + 3000

    def snippet(self, label, pk):
        self.seen.append((label, pk))
        return pk + 4000

    def richtext(self, html):
        return html.replace('id="1"', 'id="99"') if isinstance(html, str) else html


class StreamWalkTests(SimpleTestCase):

    @staticmethod
    def _stream(model, field='body'):
        return model._meta.get_field(field).stream_block

    def test_homepage_choosers_are_all_remapped(self):
        # image_override was collected on export but never remapped on import,
        # because the two sides walked streams with different code.
        from home.models import HomePage

        value = [
            {'type': 'featured_article', 'id': 'a', 'value': {
                'source': 'pick', 'pick': {'item': 5, 'image_override': 6},
            }},
            {'type': 'article_group', 'id': 'b', 'value': {
                'heading': 'x', 'more_link_page': 11,
                'source': {
                    'topic': 7, 'volume': 8, 'issue': 9,
                    'picks': [{'type': 'item', 'id': 'c',
                               'value': {'item': 10, 'image_override': None}}],
                },
            }},
        ]
        mapper = _Recorder()
        out = map_stream(self._stream(HomePage), value, mapper)

        pick = out[0]['value']['pick']
        self.assertEqual((pick['item'], pick['image_override']), (3005, 1006))
        group = out[1]['value']
        self.assertEqual(group['more_link_page'], 3011)
        self.assertEqual(
            (group['source']['topic'], group['source']['volume'], group['source']['issue']),
            (4007, 4008, 3009),
        )
        self.assertEqual(group['source']['picks'][0]['value']['item'], 3010)
        self.assertIsNone(group['source']['picks'][0]['value']['image_override'])
        self.assertIn(('issue.topic', 7), mapper.seen)
        self.assertIn(('issue.volume', 8), mapper.seen)
        # the input is left as it was
        self.assertEqual(value[0]['value']['pick']['item'], 5)

    def test_article_body_media_and_rich_text(self):
        from articles.models import Article

        value = [
            {'type': 'image', 'id': '1', 'value': {
                'image': 5, 'caption': '<p><embed embedtype="image" id="1"/></p>',
            }},
            {'type': 'paragraph', 'id': '2', 'value': '<p><embed embedtype="image" id="1"/></p>'},
            {'type': 'document', 'id': '3', 'value': 7},
            {'type': 'audio', 'id': '4', 'value': {'audio_file': 8, 'caption': 'c'}},
        ]
        out = map_stream(self._stream(Article), value, _Recorder())

        self.assertEqual(out[0]['value']['image'], 1005)
        self.assertIn('id="99"', out[0]['value']['caption'])
        self.assertEqual(out[1]['value'], '<p><embed embedtype="image" id="99"/></p>')
        self.assertEqual(out[2]['value'], 2007)
        self.assertEqual(out[3]['value']['audio_file'], 2008)

    def test_a_removed_block_type_is_remapped_by_field_name(self):
        from articles.models import Article

        value = [{'type': 'retired_block', 'id': 'z',
                  'value': {'image': 5, 'video_file': 6, 'caption': 'x'}}]
        out = map_stream(self._stream(Article), value, _Recorder())
        self.assertEqual(out[0]['value'], {'image': 1005, 'video_file': 2006, 'caption': 'x'})

    def test_without_definitions_the_name_walk_is_used(self):
        value = [{'type': 'callout', 'value': {'image_override': 5, 'text': 'x'}}]
        out = map_stream(None, value, _Recorder())
        self.assertEqual(out[0]['value']['image_override'], 1005)

    def test_a_walk_error_is_reported_and_the_value_kept(self):
        from articles.models import Article

        errors = []
        out = map_stream(self._stream(Article), 5, _Recorder(), on_error=errors.append)
        self.assertEqual(out, 5)
        self.assertEqual(len(errors), 1)


# ---------------------------------------------------------------------------
# Import resolver / applicator
# ---------------------------------------------------------------------------

class ImportResolverTests(TestCase):

    def _resolver(self, **kwargs):
        from home.management.commands.import_content import _Resolver

        self.console = _quiet_console()
        return _Resolver(kwargs.pop('images', {}), kwargs.pop('docs', {}), self.console, **kwargs)

    def test_user_foreign_keys_are_never_taken_from_an_archive(self):
        # Older archives carried Literati.reader_user as a raw PK: imported, it
        # attached the author page (and its phone/email sync) to whichever
        # account had that PK on this database.
        from home.management.commands.import_content import _Applicator
        from literati.models import Literati
        from reader.models import ReaderUser

        bystander = ReaderUser.objects.create_user(phone_number='+919876500001', name='Bystander')
        resolver = self._resolver()
        page = Literati(title='Writer', slug='writer')

        _Applicator(resolver, self.console).apply(
            page, {'reader_user': bystander.pk, 'role': 'Poet'}
        )

        self.assertIsNone(page.reader_user_id)
        self.assertEqual(page.role, 'Poet')

        # Re-importing over an existing author (archives write the FK as
        # null) must not detach the account it is linked to here.
        page.reader_user = bystander
        _Applicator(resolver, self.console).apply(page, {'reader_user': None})
        self.assertEqual(page.reader_user_id, bystander.pk)

    def test_a_bare_foreign_key_is_left_empty(self):
        from articles.models import Article
        from home.management.commands.import_content import _Applicator
        from issue.models import Topic

        Topic.objects.create(name='Other', slug='other')
        resolver = self._resolver()
        page = Article(title='x', slug='x', date=date(2026, 1, 1))

        _Applicator(resolver, self.console).apply(page, {'topic': Topic.objects.get().pk})

        self.assertIsNone(page.topic_id)
        self.assertEqual(self.console.warnings, 1)

    def test_snippet_references_are_allow_listed(self):
        from issue.models import Topic

        topic = Topic.objects.create(name='Science', slug='science')
        resolver = self._resolver()

        self.assertEqual(
            resolver.resolve_snippet({'model': 'issue.topic', 'key': {'slug': 'science'}}), topic.pk
        )
        for ref in (
            {'model': 'reader.readeruser', 'key': {'pk': 1}},           # not a snippet
            {'model': 'issue.topic', 'key': {'name__startswith': 'S'}},  # not its key
            {'model': 'issue.topic', 'key': {'slug': 'science', 'pk': topic.pk}},
            {'model': 'issue.topic', 'key': 'science'},
        ):
            with self.subTest(ref=ref):
                self.assertIsNone(resolver.resolve_snippet(ref))

    def test_unmappable_ids_become_empty_in_a_1_1_archive(self):
        resolver = self._resolver(
            images={5: 50},
            page_paths={'3': 'home/not-here'},
            snippet_keys={'issue.topic': {}},
        )
        self.assertEqual(resolver.image_pk(5), 50)
        self.assertIsNone(resolver.image_pk(6))
        self.assertIsNone(resolver.page_pk(3))
        self.assertIsNone(resolver.snippet_pk('issue.topic', 1))
        self.assertEqual(
            resolver.rewrite_richtext('<a linktype="page" id="3">x</a><embed embedtype="image" id="5"/>'),
            '<a linktype="page" id="0">x</a><embed embedtype="image" id="50"/>',
        )

    def test_an_unmapped_reference_still_renders(self):
        # Why unmapped ids become 0 and not "": Wagtail hands the id to
        # in_bulk(), which rejects "" — the page then fails to save or render.
        from wagtail.rich_text import expand_db_html

        resolver = self._resolver(page_paths={})
        html = resolver.rewrite_richtext(
            '<p><a linktype="page" id="3">gone</a><embed embedtype="image" id="4" format="left" alt=""/></p>'
        )
        self.assertEqual(expand_db_html(html), '<p><a>gone</a><img alt=""></p>')
        with self.assertRaises(ValueError):
            expand_db_html('<a linktype="page" id="">x</a>')

    def test_a_1_0_archive_keeps_page_and_snippet_ids(self):
        resolver = self._resolver()
        self.assertEqual(resolver.page_pk(3), 3)
        self.assertEqual(resolver.snippet_pk('issue.topic', 4), 4)

    def test_bare_pks_in_m2m_lists_are_skipped(self):
        resolver = self._resolver(images={5: 50})
        self.assertEqual(resolver.resolve_m2m([7, {'__ref': 'image', 'pk': 5}]), [50])


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------

class _ArchiveTestCase(TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='icarus-archive-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.archive = os.path.join(self.tmp, 'site.zip')
        self.out = self.err = None

    def _export(self, *args, **kwargs):
        call_command(
            'export_content', *args, output=self.archive, no_progress=True,
            verbosity=0, stdout=io.StringIO(), stderr=io.StringIO(), **kwargs,
        )
        with zipfile.ZipFile(self.archive) as zf:
            return json.loads(zf.read('manifest.json'))

    def _import(self, **kwargs):
        """Run import_content with publishing side effects observed, not run."""
        self.out, self.err = io.StringIO(), io.StringIO()
        kwargs.setdefault('yes', True)
        kwargs.setdefault('verbosity', 1)
        with mock.patch('articles.signals.async_task') as self.tts, \
                mock.patch('django_q.tasks.async_task') as self.queued, \
                self.captureOnCommitCallbacks(execute=True):
            call_command(
                'import_content', self.archive, no_progress=True,
                stdout=self.out, stderr=self.err, **kwargs,
            )
        return self.out.getvalue()

    def _write_archive(self, manifest=None, files=None):
        data = {
            'version': '1.1', 'created_at': '2026-10-01T00:00:00',
            'wagtail_version': '.'.join(str(v) for v in wagtail.VERSION[:3]),
            'scope': 'full', 'personal_data': False, 'site': {},
            'images': [], 'documents': [], 'snippets': {}, 'pages': [],
            'page_paths': {}, 'snippet_keys': {},
        }
        data.update(manifest or {})
        with zipfile.ZipFile(self.archive, 'w') as zf:
            zf.writestr('manifest.json', json.dumps(data))
            for name, content in (files or {}).items():
                zf.writestr(name, content)

    @staticmethod
    def _media_files():
        found = set()
        for dirpath, _dirs, names in os.walk(settings.MEDIA_ROOT):
            found.update(os.path.join(dirpath, n) for n in names)
        return found

    @staticmethod
    def _hash(media):
        with media.open_file() as f:
            return hash_filelike(f)

    @staticmethod
    def _wipe_to_fresh_install():
        """Back to what `migrate` leaves: Wagtail's Welcome page and a default Site."""
        from issue.models import Topic, Volume

        root = Page.get_first_root_node()
        site = Site.objects.get(is_default_site=True)
        site.root_page = root
        site.save()
        with warnings.catch_warnings():
            # Wagtail warns while deleting a subtree whose rows go first
            warnings.simplefilter('ignore', RuntimeWarning)
            for child in root.get_children():
                child.delete()
        welcome = Page(title='Welcome to your new Wagtail site!', slug='home')
        root.add_child(instance=welcome)
        site.root_page = welcome
        site.hostname, site.port, site.site_name = 'dest.example', 8443, 'Fresh install'
        site.save()
        get_image_model().objects.all().delete()
        get_document_model().objects.all().delete()
        Topic.objects.all().delete()
        Volume.objects.all().delete()


class ContentRoundTripTests(_ArchiveTestCase):

    def _build_site(self):
        from articles.models import Article, ArticleIndexPage
        from home.models import HomePage
        from issue.models import Issue, IssueIndexPage, Topic, Volume
        from literati.models import ArticleAuthorRelationship, AuthorIndexPage, Literati

        root = Page.get_first_root_node()
        site = Site.objects.get(is_default_site=True)
        site.root_page = root
        site.save()
        Page.objects.get(depth=2, slug='home').delete()
        home = HomePage(title='Home', slug='home')
        root.add_child(instance=home)
        site.root_page = home
        site.hostname, site.site_name = 'source.example', 'Sasthragathi'
        site.save()

        Image = get_image_model()
        # Same title, different pictures: matching by title used to fold them into one.
        self.cover = Image.objects.create(title='photo', file=get_test_image_file('cover.png', colour='white'))
        self.inline = Image.objects.create(title='photo', file=get_test_image_file('inline.png', colour='red'))
        self.pdf = get_document_model().objects.create(
            title='Report', file=ContentFile(b'%PDF-1.4 report', name='report.pdf')
        )
        self.hashes = {'cover': self._hash(self.cover), 'inline': self._hash(self.inline),
                       'pdf': self._hash(self.pdf)}
        topic = Topic.objects.create(name='Science', slug='science')
        Volume.objects.create(number=1, year_start=2026)

        authors = AuthorIndexPage(title='Authors', slug='authors')
        home.add_child(instance=authors)
        writer = Literati(
            title='Writer', slug='writer', phone_number='+919876500002',
            email='writer@example.com', city='Kochi',
        )
        authors.add_child(instance=writer)

        articles = ArticleIndexPage(title='Articles', slug='articles')
        home.add_child(instance=articles)
        first = Article(title='First', slug='first', date=date(2026, 1, 1), topic=topic,
                        cover_image=self.cover)
        articles.add_child(instance=first)
        second = Article(title='Second', slug='second', date=date(2026, 1, 2))
        articles.add_child(instance=second)

        issues = IssueIndexPage(title='Issues', slug='issues')
        home.add_child(instance=issues)
        issue = Issue(title='Issue 1', slug='issue-1', date_of_publishing=date(2026, 1, 1))
        issues.add_child(instance=issue)

        # Forward references: both point at pages that sort later in the tree.
        first.main_issue = issue
        first.body = json.dumps([
            {'type': 'image', 'id': 'b1', 'value': {'image': self.cover.pk}},
            {'type': 'paragraph', 'id': 'b2', 'value': (
                f'<p>See <embed embedtype="image" id="{self.inline.pk}" format="left" alt="x"/>'
                f' and <a linktype="page" id="{second.pk}">the next piece</a>.</p>'
            )},
            {'type': 'document', 'id': 'b3', 'value': self.pdf.pk},
        ])
        first.save()
        first.tags.add('physics', 'space')
        first.save()
        ArticleAuthorRelationship.objects.create(page=first, author=writer, role='Author')

        home.body = json.dumps([{'type': 'featured_article', 'id': 'h1', 'value': {
            'source': 'pick', 'pick': {'item': second.pk, 'image_override': self.inline.pk},
        }}])
        home.save()

        from literati.models import EditorialBoard, EditorialBoardMember
        board = EditorialBoard.objects.create(name='Board 2025', is_current=False)
        EditorialBoardMember.objects.create(
            board=board, editor=writer, role='editor', joined_at=date(2025, 1, 1)
        )

    def test_round_trip_onto_a_fresh_install(self):
        from articles.models import Article
        from home.models import HomePage
        from issue.models import Issue
        from literati.models import Literati

        self._build_site()
        manifest = self._export()
        self._wipe_to_fresh_install()

        out = self._import()

        # Wagtail's Welcome page was replaced by the real HomePage, and the
        # default Site (which cascades from its root page) survived it.
        home = Page.objects.get(depth=2, slug='home')
        self.assertIs(home.specific_class, HomePage)
        site = Site.objects.get(is_default_site=True)
        self.assertEqual(site.root_page_id, home.pk)
        # This server's own address is kept; only the name comes across.
        self.assertEqual((site.hostname, site.port, site.site_name), ('dest.example', 8443, 'Sasthragathi'))

        Image = get_image_model()
        self.assertEqual(Image.objects.count(), 2)
        cover = Image.objects.get(file_hash=self.hashes['cover'])
        inline = Image.objects.get(file_hash=self.hashes['inline'])
        pdf = get_document_model().objects.get(file_hash=self.hashes['pdf'])

        first = Article.objects.get(slug='first')
        second = Article.objects.get(slug='second')
        issue = Issue.objects.get(slug='issue-1')
        writer = Literati.objects.get(slug='writer')

        self.assertEqual(first.cover_image_id, cover.pk)
        self.assertEqual(first.topic.slug, 'science')
        self.assertEqual(first.main_issue_id, issue.pk)

        body = list(first.body.raw_data)
        self.assertEqual(body[0]['value']['image'], cover.pk)
        self.assertIn(f'<embed embedtype="image" id="{inline.pk}"', body[1]['value'])
        self.assertIn(f'<a linktype="page" id="{second.pk}">', body[1]['value'])
        self.assertEqual(body[2]['value'], pdf.pk)

        pick = home.specific.body.raw_data[0]['value']['pick']
        self.assertEqual((pick['item'], pick['image_override']), (second.pk, inline.pk))

        self.assertEqual(
            [(r.author_id, r.role) for r in first.article_authors.all()], [(writer.pk, 'Author')]
        )
        self.assertEqual(sorted(first.tags.names()), ['physics', 'space'])

        # What is live is the imported content, in a new revision.
        self.assertTrue(first.live)
        self.assertEqual(first.live_revision_id, first.latest_revision_id)
        live = first.live_revision.as_object()
        self.assertEqual(live.body.raw_data[0]['value']['image'], cover.pk)
        self.assertEqual(live.article_authors.count(), 1)

        # Contact details were not in the archive, and no account was attached.
        self.assertFalse(manifest['personal_data'])
        writer_row = next(p for p in manifest['pages'] if p['slug'] == 'writer')
        for field in ('phone_number', 'email', 'city'):
            self.assertNotIn(field, writer_row['specific_fields'])
        self.assertEqual((writer.email, writer.city), ('', ''))
        self.assertFalse(writer.phone_number)
        self.assertIsNone(writer.reader_user_id)

        # Publishing during the import queued no text-to-speech and no newsletter…
        self.tts.assert_not_called()
        self.assertNotIn(
            'reader.tasks.send_issue_newsletter_task',
            [c.args[0] for c in self.queued.call_args_list if c.args],
        )
        self.assertIn('1 imported issue(s) were never mailed', out)
        # …and publishing works normally again afterwards.
        with mock.patch('articles.signals.async_task') as tts_after:
            second.save_revision().publish()
        tts_after.assert_called_once_with('articles.tasks.generate_article_audio_task', second.pk)

    def test_board_history_comes_across_without_an_extra_row(self):
        # Attaching a member used to write a "joined today" history row next
        # to the real ones the archive carries.
        from literati.models import EditorialBoardMembershipHistory

        self._build_site()
        self._export()
        self._wipe_to_fresh_install()
        self._import()

        history = EditorialBoardMembershipHistory.objects.filter(editor__slug='writer')
        self.assertEqual(
            list(history.values_list('board__name', 'role', 'joined_at', 'left_at')),
            [('Board 2025', 'editor', date(2025, 1, 1), None)],
        )

    def test_reimport_publishes_the_archive_not_an_older_revision(self):
        # The old importer published get_latest_revision() — this database's
        # previous revision — over the fields it had just applied.
        from articles.models import Article

        self._build_site()
        self._export()
        local = Article.objects.get(slug='first')
        local.title = 'Edited here'
        with mock.patch('articles.signals.async_task'):
            local.save_revision().publish()

        self._import()

        first = Article.objects.get(slug='first')
        self.assertEqual(first.title, 'First')
        self.assertEqual(first.live_revision.as_object().title, 'First')

    def test_personal_data_only_with_the_opt_in(self):
        self._build_site()
        manifest = self._export(include_personal_data=True)

        self.assertTrue(manifest['personal_data'])
        writer_row = next(p for p in manifest['pages'] if p['slug'] == 'writer')
        self.assertEqual(writer_row['specific_fields']['email'], 'writer@example.com')
        self.assertIsNone(writer_row['specific_fields'].get('reader_user'))
        self.assertNotIn('tags', writer_row['specific_fields'])  # exported by name under 'tags'

    def test_importing_twice_creates_nothing_new(self):
        from literati.models import ArticleAuthorRelationship

        self._build_site()
        self._export()
        self._wipe_to_fresh_install()
        self._import()

        def counts():
            return (
                get_image_model().objects.count(), get_document_model().objects.count(),
                Page.objects.count(), ArticleAuthorRelationship.objects.count(),
            )

        before = counts()
        out = self._import()
        self.assertEqual(counts(), before)
        self.assertIn('0 created · 2 reused', out)

    def test_media_without_a_stored_hash_is_still_matched(self):
        # Rows saved outside the admin (scripts, generated TTS audio) have no
        # file_hash; importing back into the same database must reuse them.
        self._build_site()
        self.assertEqual(get_image_model().objects.filter(file_hash='').count(), 2)
        self._export()

        self._import()

        self.assertEqual(get_image_model().objects.count(), 2)
        self.assertEqual(get_document_model().objects.count(), 1)

    def test_dry_run_writes_nothing(self):
        from articles.models import Article

        self._build_site()
        self._export()
        self._wipe_to_fresh_install()
        files_before = self._media_files()

        out = self._import(dry_run=True, yes=False)

        self.assertIn('Would import', out)
        self.assertFalse(Article.objects.exists())
        self.assertFalse(get_image_model().objects.exists())
        self.assertEqual(self._media_files(), files_before)


class ImportFailureTests(_ArchiveTestCase):

    def _png(self, colour='blue'):
        return get_test_image_file('x.png', colour=colour).file.getvalue()

    def _archive_with_a_broken_page(self):
        self._write_archive(
            {
                'images': [{'pk': 1, 'title': 'kept-or-not', 'file': 'media/images/1/x.png'}],
                'snippets': {'issue.volume': [{'number': 7, 'year_start': 2026}]},
                'pages': [{
                    'model': 'issue.issueindexpage', 'slug': 'lost', 'slug_path': 'nowhere/lost',
                    'parent_slug_path': 'nowhere', 'base_fields': {'title': 'Lost', 'slug': 'lost'},
                    'specific_fields': {}, 'children': {}, 'tags': {},
                }],
            },
            files={'media/images/1/x.png': self._png()},
        )

    def test_a_failure_rolls_everything_back_and_exits_non_zero(self):
        from issue.models import Volume

        self._archive_with_a_broken_page()
        files_before = self._media_files()

        with self.assertRaisesMessage(CommandError, 'nothing was written'):
            self._import()

        self.assertFalse(Volume.objects.filter(number=7).exists())
        self.assertFalse(get_image_model().objects.filter(title='kept-or-not').exists())
        # the image file written before the failure is gone too
        self.assertEqual(self._media_files(), files_before)
        self.assertIn('page [nowhere/lost]', self.err.getvalue())

    def test_tolerant_commits_the_rest_and_still_exits_non_zero(self):
        from issue.models import Volume

        self._archive_with_a_broken_page()

        with self.assertRaisesMessage(CommandError, 'everything else is committed'):
            self._import(tolerant=True)

        self.assertTrue(Volume.objects.filter(number=7).exists())
        self.assertTrue(get_image_model().objects.filter(title='kept-or-not').exists())
        self.assertIn('page [nowhere/lost]', self.err.getvalue())
        self.assertIn('pages     : 0 created · 0 updated · 1 failed', self.out.getvalue())

    def test_refuses_to_run_unattended_without_yes(self):
        from issue.models import Volume

        self._write_archive({'snippets': {'issue.volume': [{'number': 7, 'year_start': 2026}]}})

        with mock.patch('sys.stdin') as stdin:
            stdin.isatty.return_value = False
            with self.assertRaisesMessage(CommandError, '--yes'):
                self._import(yes=False)

        self.assertFalse(Volume.objects.filter(number=7).exists())

    def test_media_gets_the_same_checks_as_an_admin_upload(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        self._write_archive(
            {
                'images': [
                    {'pk': 1, 'title': 'logo', 'file': 'media/images/1/logo.svg'},
                    {'pk': 2, 'title': 'fake', 'file': 'media/images/2/fake.png'},
                    {'pk': 3, 'title': 'real', 'file': 'media/images/3/real.png'},
                    # a real PNG under a name the browser would run as a page
                    {'pk': 5, 'title': 'disguised', 'file': 'media/images/5/page.html'},
                ],
                'documents': [{'pk': 4, 'title': 'tool', 'file': 'media/documents/4/tool.exe'}],
            },
            files={
                'media/images/1/logo.svg': svg,
                'media/images/2/fake.png': b'not a picture',
                'media/images/3/real.png': self._png(),
                'media/images/5/page.html': self._png('green'),
                'media/documents/4/tool.exe': b'MZ',
            },
        )

        with self.assertRaises(CommandError):
            self._import(tolerant=True)

        titles = set(get_image_model().objects.values_list('title', flat=True))
        self.assertEqual(titles, {'real'})
        self.assertFalse(get_document_model().objects.exists())
        self.assertIn('4 rejected by upload checks', self.out.getvalue())
        real = get_image_model().objects.get(title='real')
        self.assertEqual((real.width, real.height), (640, 480))  # read from the file

    def test_an_image_over_the_upload_limit_is_rejected_unread(self):
        self._write_archive(
            {'images': [{'pk': 1, 'title': 'huge', 'file': 'media/images/1/huge.png'}]},
            files={'media/images/1/huge.png': self._png()},
        )
        with self.settings(WAGTAILIMAGES_MAX_UPLOAD_SIZE=100), \
                mock.patch('zipfile.ZipFile.read', side_effect=zipfile.ZipFile.read, autospec=True) as read:
            with self.assertRaises(CommandError):
                self._import(tolerant=True)

        self.assertFalse(get_image_model().objects.exists())
        self.assertIn('over the 100-byte upload limit', self.err.getvalue())
        read_names = [getattr(c.args[1], 'filename', c.args[1]) for c in read.call_args_list]
        self.assertIn('manifest.json', read_names)
        self.assertNotIn('media/images/1/huge.png', read_names)

    def test_an_unknown_archive_format_is_refused(self):
        self._write_archive({'version': '2.0'})
        with self.assertRaisesMessage(CommandError, 'not one this importer understands'):
            self._import()

    def test_a_page_of_another_type_at_the_same_path_is_an_error(self):
        from literati.models import AuthorIndexPage

        welcome = Page.objects.get(depth=2, slug='home')
        welcome.add_child(instance=AuthorIndexPage(title='Writers', slug='articles'))
        self._write_archive({'pages': [{
            'model': 'articles.articleindexpage', 'slug': 'articles', 'slug_path': 'home/articles',
            'parent_slug_path': 'home', 'base_fields': {'title': 'Articles', 'slug': 'articles'},
            'specific_fields': {}, 'children': {}, 'tags': {},
        }]})

        with self.assertRaisesMessage(CommandError, 'nothing was written'):
            self._import()

        self.assertIn('not a ArticleIndexPage', self.err.getvalue())
        self.assertEqual(Page.objects.get(slug='articles').specific_class, AuthorIndexPage)
        self.assertEqual(Page.objects.get(slug='articles').title, 'Writers')


class CheckArchiveTests(_ArchiveTestCase):

    def _check(self):
        out = io.StringIO()
        call_command('check_archive', self.archive, stdout=out)
        return out.getvalue()

    def test_reports_personal_data_and_format(self):
        self._write_archive({'personal_data': True})
        out = self._check()
        self.assertIn('personal : YES', out)
        self.assertIn('No structural issues detected', out)

        self._write_archive({'version': '1.0'})
        self.assertIn('format 1.0: no author credits', self._check())

        self._write_archive({'version': '2.0'})
        self.assertIn('cannot be imported by this release', self._check())
