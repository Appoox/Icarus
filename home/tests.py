from home.models import HomePage

from django.test import TestCase
from wagtail.models import Page, Site
from wagtail.test.utils import WagtailPageTestCase


class HomeSetUpTests(WagtailPageTestCase):
    """
    Tests for basic page structure setup and HomePage creation.
    """

    def test_root_create(self):
        root_page = Page.objects.get(pk=1)
        self.assertIsNotNone(root_page)

    def test_homepage_create(self):
        root_page = Page.objects.get(pk=1)
        homepage = HomePage(title="Home")
        root_page.add_child(instance=homepage)
        self.assertTrue(HomePage.objects.filter(title="Home").exists())


class HomeTests(WagtailPageTestCase):
    """
    Tests for homepage functionality and rendering.
    """

    def setUp(self):
        """
        Create a homepage instance for testing.
        """
        root_page = Page.get_first_root_node()
        Site.objects.create(hostname="testsite", root_page=root_page, is_default_site=True)
        self.homepage = HomePage(title="Home")
        root_page.add_child(instance=self.homepage)

    def test_homepage_is_renderable(self):
        self.assertPageIsRenderable(self.homepage)

    def test_homepage_template_used(self):
        response = self.client.get(self.homepage.url)
        self.assertTemplateUsed(response, "home/home_page.html")


class HomepageLayoutSchemaTests(WagtailPageTestCase):
    """
    Regression tests for the Homepage Layout canvas payload.

    The canvas ships its whole state to the browser through json_script, so
    a single value anywhere in the payload that json.dumps cannot handle is
    not a degraded control — it is a 500 on the entire screen, before any of
    it renders.  Block defaults are the usual source: a RichTextBlock's
    default is a `RichText` object, an EmbedBlock's is an `EmbedValue`.

    The second test is the one that matters more.  Making the payload
    serialisable is easy if you are willing to throw values away; the point
    is that a RichText has to survive as its *source HTML*, because whatever
    the canvas hands the browser is what the browser hands back on save.
    Coerce it to None and the page renders perfectly while quietly erasing
    the editor's copy on their next click.
    """

    def test_every_block_type_produces_a_json_serialisable_schema(self):
        import json

        from home.blocks import HOMEPAGE_BLOCKS
        from home.layout_views import field_schema

        for name, block in HOMEPAGE_BLOCKS:
            with self.subTest(block=name):
                json.dumps(field_schema(block, name, None))

    def test_palette_is_json_serialisable(self):
        import json

        from home.layout_views import _palette

        json.dumps(_palette())

    def test_rich_text_survives_the_schema_round_trip(self):
        from home.blocks import HOMEPAGE_BLOCKS
        from home.layout_views import field_schema

        html = "<p>Editors wrote <b>this</b>.</p>"
        block = dict(HOMEPAGE_BLOCKS)["rich_text"]

        schema = field_schema(block, "rich_text", html)
        self.assertEqual(schema["value"], html)

        # And the value the canvas would post back is still valid input.
        self.assertEqual(block.to_python(schema["value"]).source, html)


class ExportContentStreamFieldTests(WagtailPageTestCase):
    """
    Regression tests for export_content.

    A bad merge dropped _Serialiser._IMAGE_FIELD_NAMES, so every StreamField
    scan raised AttributeError, serialize() swallowed it and left the field
    out — every article body vanished from every export while the command
    still reported success.
    """

    def _serialiser(self):
        from io import StringIO

        from django.core.management.color import no_style

        from home.management.commands._console import Console
        from home.management.commands.export_content import (
            _MediaRegistry,
            _Serialiser,
        )

        console = Console(StringIO(), StringIO(), no_style(), verbosity=0, progress=False)
        return _Serialiser(_MediaRegistry(console), console), console

    def test_article_body_is_exported(self):
        from datetime import date

        from wagtail.rich_text import RichText

        from articles.models import Article

        root = Page.get_first_root_node()
        article = Article(
            title="Exported", slug="exported", date=date(2026, 1, 1),
            body=[("paragraph", RichText("<p>Body that must survive export</p>"))],
        )
        root.add_child(instance=article)

        serialiser, console = self._serialiser()
        data = serialiser.serialize(article)

        self.assertIn("body", data)
        self.assertEqual(data["body"][0]["type"], "paragraph")
        self.assertIn("Body that must survive export", data["body"][0]["value"])
        self.assertEqual(console.warnings, 0)

    def test_named_image_fields_are_collected(self):
        from wagtail.images import get_image_model
        from wagtail.images.tests.utils import get_test_image_file

        image = get_image_model().objects.create(title="cover", file=get_test_image_file())
        serialiser, _console = self._serialiser()

        serialiser._scan_generic(
            [{"type": "callout", "value": {"image_override": image.pk, "text": "x"}}]
        )

        self.assertIn(image.pk, serialiser._media.images)


class CacheTableMigrationTests(TestCase):
    """
    The cache lives in a database table that `migrate` never created: a
    database migrated without `createcachetable` failed on every page with
    'relation "icarus_cache" does not exist'. Migration home 0019 creates it.
    """

    def _create(self, table):
        import importlib
        from types import SimpleNamespace

        from django.db import connection

        migration = importlib.import_module("home.migrations.0019_create_cache_table")
        caches = {"default": {"BACKEND": "django.core.cache.backends.db.DatabaseCache", "LOCATION": table}}
        with self.settings(CACHES=caches):
            migration.create_cache_table(None, SimpleNamespace(connection=connection))
        return connection.introspection.table_names()

    def test_migrate_creates_the_cache_table(self):
        self.assertIn("icarus_cache_migration_test", self._create("icarus_cache_migration_test"))

    def test_it_leaves_an_existing_table_alone(self):
        from django.db import connection

        self._create("icarus_cache_migration_test")
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO icarus_cache_migration_test (cache_key, value, expires) VALUES (%s, %s, now())",
                ["kept", "x"],
            )
        self._create("icarus_cache_migration_test")  # second run: no error
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM icarus_cache_migration_test")
            self.assertEqual(cursor.fetchone()[0], 1)

    def test_settings_use_that_table(self):
        from django.conf import settings

        # base.py's cache; the test settings swap in locmem, so read the module.
        import Icarus.settings.base as base

        self.assertEqual(base.CACHES["default"]["LOCATION"], "icarus_cache")
        self.assertIn("locmem", settings.CACHES["default"]["BACKEND"].lower())


class SettingsModuleTests(WagtailPageTestCase):
    """
    The live site ran Icarus.settings.dev because production.py could not
    start on its own. Each module is imported in a clean subprocess so the
    environment it sees is exactly the one given here.
    """

    REQUIRED = {"SECRET_KEY": "x", "SALT_KEY": "y", "PHONE_HASH_KEY": "z"}

    def _import(self, module, expr, **env):
        import os
        import subprocess
        import sys

        from django.conf import settings

        # A clean environment and no .env: the module sees exactly `env`.
        run_env = {"PATH": os.environ.get("PATH", ""), "ICARUS_DOTENV": "", **env}
        return subprocess.run(
            [sys.executable, "-c", f"import {module} as s; print({expr})"],
            cwd=settings.BASE_DIR, env=run_env, capture_output=True, text=True,
        )

    def test_production_starts_with_only_env_secrets(self):
        out = self._import(
            "Icarus.settings.production",
            "s.DEBUG, '*' in s.ALLOWED_HOSTS, s.STORAGES['staticfiles']['BACKEND'].rsplit('.', 1)[1]",
            **self.REQUIRED,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "False False ManifestStaticFilesStorage")

    def test_production_refuses_to_start_without_a_secret(self):
        for missing in self.REQUIRED:
            env = {k: v for k, v in self.REQUIRED.items() if k != missing}
            with self.subTest(missing=missing):
                out = self._import("Icarus.settings.production", "s.DEBUG", **env)
                self.assertNotEqual(out.returncode, 0)
                self.assertIn(missing, out.stderr)

    def test_production_module_does_not_change_base_storages(self):
        out = self._import(
            "Icarus.settings.base",
            "s.STORAGES['staticfiles']['BACKEND'].rsplit('.', 1)[1] if __import__('Icarus.settings.production') else ''",
            **self.REQUIRED,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "StaticFilesStorage")

    def test_dev_does_not_accept_every_host(self):
        out = self._import("Icarus.settings.dev", "'*' in s.ALLOWED_HOSTS, 'localhost' in s.ALLOWED_HOSTS", **self.REQUIRED)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "False True")

    def _dotenv(self, text):
        import os
        import tempfile

        f = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False)
        f.write(text)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_dev_mail_stays_on_this_machine(self):
        # A native .env often holds the server's SMTP settings; with a copy of
        # the live database, a test newsletter would reach real readers.
        out = self._import(
            "Icarus.settings.dev", "s.EMAIL_BACKEND",
            EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend", **self.REQUIRED,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "django.core.mail.backends.console.EmailBackend")

        out = self._import(
            "Icarus.settings.dev", "s.EMAIL_BACKEND",
            DEV_EMAIL_BACKEND="django.core.mail.backends.filebased.EmailBackend", **self.REQUIRED,
        )
        self.assertEqual(out.stdout.strip(), "django.core.mail.backends.filebased.EmailBackend")

    def test_dev_reads_the_dotenv_file(self):
        path = self._dotenv(
            "SECRET_KEY=from-dotenv\nSALT_KEY=s\nPHONE_HASH_KEY=p\nMOCK_PAYMENTS_ENABLED=True\n"
        )
        out = self._import(
            "Icarus.settings.dev", "s.SECRET_KEY, s.MOCK_PAYMENTS_ENABLED", ICARUS_DOTENV=path,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "from-dotenv True")

        # A variable set in the environment wins over the file.
        out = self._import(
            "Icarus.settings.dev", "s.SECRET_KEY", ICARUS_DOTENV=path, SECRET_KEY="from-env",
        )
        self.assertEqual(out.stdout.strip(), "from-env")

    def test_the_test_settings_ignore_a_developers_dotenv(self):
        # Natively, a .env sits in the checkout. Only its database connection
        # may reach the tests; anything else could change what they check.
        path = self._dotenv(
            "SECRET_KEY=from-dotenv\nMOCK_PAYMENTS_ENABLED=True\nFREE_ARTICLE_LIMIT=99\n"
            "DATABASE_URL=postgres://dev:pw@dbhost:5432/devdb\n"
        )
        out = self._import(
            "Icarus.settings.test",
            "s.SECRET_KEY, s.MOCK_PAYMENTS_ENABLED, s.FREE_ARTICLE_LIMIT, "
            "s.DATABASES['default']['HOST'], s.DATABASES['default']['NAME']",
            ICARUS_DOTENV=path,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(
            out.stdout.strip(), "test-secret-key-not-for-production False 3 dbhost devdb"
        )

    def test_hosts_come_from_the_environment(self):
        out = self._import(
            "Icarus.settings.production", "s.ALLOWED_HOSTS",
            ALLOWED_HOSTS="a.example,b.example", **self.REQUIRED,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "['a.example', 'b.example']")

    def test_production_cookies_are_https_only_and_queue_retries_are_bounded(self):
        out = self._import(
            "Icarus.settings.production",
            "s.SESSION_COOKIE_SECURE, s.CSRF_COOKIE_SECURE, s.SECURE_PROXY_SSL_HEADER, "
            "s.Q_CLUSTER['max_attempts'], s.Q_CLUSTER['ack_failures'], s.Q_CLUSTER['catch_up'], "
            "s.MIDDLEWARE[0]",
            **self.REQUIRED,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(
            out.stdout.strip(),
            "True True ('HTTP_X_FORWARDED_PROTO', 'https') 3 True False Icarus.health.HealthCheckMiddleware",
        )

    def test_cache_is_shared_between_workers(self):
        out = self._import(
            "Icarus.settings.production", "s.CACHES['default']['BACKEND'].rsplit('.', 1)[1]", **self.REQUIRED,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "DatabaseCache")

    def test_entrypoints_default_to_production(self):
        from django.conf import settings

        for name in ("manage.py", "Icarus/wsgi.py", "Icarus/asgi.py"):
            with self.subTest(file=name):
                source = (settings.BASE_DIR / name).read_text()
                self.assertIn('"DJANGO_SETTINGS_MODULE", "Icarus.settings.production"', source)


class ReportAccessPermissionTests(WagtailPageTestCase):
    """
    The audit log holds readers' personal data. is_staff alone no longer
    opens it, because every current board member is made staff
    automatically; access needs the permission (directly or via a group).
    """

    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group, Permission

        User = get_user_model()
        self.board_staff = User.objects.create_user(
            phone_number="+918800440001", name="Board", is_staff=True, staff_granted_by_board=True
        )
        self.chief = User.objects.create_user(phone_number="+918800440002", name="Chief")
        group = Group.objects.create(name="Chief Editor (test)")
        group.permissions.add(*Permission.objects.filter(
            content_type__app_label="home", codename__in=["view_auditlog", "view_analytics"]
        ))
        self.chief.groups.add(group)
        self.superuser = User.objects.create_superuser(phone_number="+918800440003", name="Root", password="x")

    def test_staff_flag_alone_is_not_enough(self):
        from home.permissions import can_view_analytics, can_view_audit_log

        self.assertFalse(can_view_audit_log(self.board_staff))
        self.assertFalse(can_view_analytics(self.board_staff))

    def test_group_permission_and_superuser_grant_access(self):
        from home.permissions import can_view_analytics, can_view_audit_log

        for user in (self.chief, self.superuser):
            self.assertTrue(can_view_audit_log(user))
            self.assertTrue(can_view_analytics(user))

    def test_manually_appointed_staff_keep_access_through_the_migration(self):
        import importlib

        from django.apps import apps
        from django.contrib.auth import get_user_model

        from home.permissions import can_view_audit_log

        User = get_user_model()
        manual = User.objects.create_user(phone_number="+918800440004", name="Manual admin", is_staff=True)
        migration = importlib.import_module("home.migrations.0017_grant_report_access_to_manual_staff")
        migration.grant(apps, None)

        manual = User.objects.get(pk=manual.pk)          # fresh permission cache
        board = User.objects.get(pk=self.board_staff.pk)
        self.assertTrue(can_view_audit_log(manual))
        self.assertFalse(can_view_audit_log(board))


class AnalyticsScriptInjectionTests(WagtailPageTestCase):
    """
    Reader-controlled strings (a print subscriber's district) were dumped
    into a <script> block with json.dumps + |safe, which does not escape
    </script>. Any reader could run code in an admin's browser.
    """

    PAYLOAD = "</script><script>alert(document.cookie)</script>"

    def test_script_json_cannot_close_the_script_tag(self):
        from home.wagtail_hooks import _script_json

        out = str(_script_json([{"district": self.PAYLOAD}]))
        self.assertNotIn("<", out)
        self.assertNotIn(">", out)
        import json
        self.assertEqual(json.loads(out), [{"district": self.PAYLOAD}])

    def test_analytics_page_does_not_echo_the_payload(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse

        User = get_user_model()
        User.objects.create_user(
            phone_number="+918800440005", name="Print reader",
            is_print_subscriber=True, district=self.PAYLOAD, state="Kerala",
        )
        self.client.force_login(User.objects.create_superuser(phone_number="+918800440006", name="Root", password="x"))

        response = self.client.get(reverse("icarus_analytics"))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(self.PAYLOAD, response.content.decode())


class AnalyticsPerformanceTests(WagtailPageTestCase):
    """
    The analytics page ran queries per gender, per age bracket, per article
    (attention score) and per recent hit. Its query count must not grow with
    the data, and the result is cached.
    """

    def setUp(self):
        from datetime import date

        from django.contrib.auth import get_user_model
        from django.utils import timezone

        from articles.models import Article

        self.User = get_user_model()
        self.Article = Article
        self.root = Page.get_first_root_node()
        self.admin = self.User.objects.create_superuser(phone_number="+918800770000", name="Root", password="x")
        self.now = timezone.now()
        self.n = 0
        self.date = date

    def _add_data(self, readers, articles):
        from django.contrib.contenttypes.models import ContentType
        from hitcount.models import Hit, HitCount

        ct = ContentType.objects.get_for_model(self.Article)
        new_articles = []
        for _ in range(articles):
            self.n += 1
            art = self.Article(title=f"A{self.n}", slug=f"a{self.n}", date=self.date(2026, 1, 1), read_fully_count=2)
            self.root.add_child(instance=art)
            hc = HitCount.objects.create(content_type=ct, object_pk=str(art.pk), hits=5)
            Hit.objects.create(hitcount=hc, ip=f"10.0.0.{self.n}", session=f"s{self.n}", user_agent="Mozilla/5.0 Chrome/126")
            new_articles.append(art)
        for i in range(readers):
            self.n += 1
            reader = self.User.objects.create_user(
                phone_number=f"+91880077{self.n:04d}", name=f"R{self.n}",
                gender=["F", "M", "X"][i % 3], gender_consent_at=self.now,
                birth_year=1960 + (i * 7) % 45, birth_year_consent_at=self.now,
            )
            reader.read_articles.add(*new_articles)
            reader.favorite_articles.add(new_articles[0])

    def _queries(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from home.wagtail_hooks import _analytics_context

        with CaptureQueriesContext(connection) as ctx:
            context = _analytics_context()
        return len(ctx.captured_queries), context

    def test_query_count_does_not_grow_with_data(self):
        self._add_data(readers=3, articles=2)
        small, _ = self._queries()
        self._add_data(readers=9, articles=6)
        large, context = self._queries()
        self.assertEqual(small, large)
        self.assertTrue(context["attention_data_json"])

    def test_gender_figures_are_correct(self):
        import json

        self._add_data(readers=3, articles=2)  # one reader each of F, M, X; 2 articles read
        context = self._queries()[1]
        labels = json.loads(str(context["gender_labels_json"]))
        hits = json.loads(str(context["gender_hits_json"]))
        self.assertEqual(sorted(labels), ["F", "M", "X"])
        self.assertEqual(hits, [2, 2, 2])
        self.assertEqual(context["with_bm_total"], 3)  # each reader bookmarked one article

    def test_page_is_cached_until_refreshed(self):
        from unittest.mock import patch

        from django.core.cache import cache
        from django.urls import reverse

        from home import wagtail_hooks

        cache.clear()
        self.client.force_login(self.admin)
        real = wagtail_hooks._analytics_context
        with patch.object(wagtail_hooks, "_analytics_context", side_effect=real) as build:
            self.client.get(reverse("icarus_analytics"))
            self.client.get(reverse("icarus_analytics"))
            self.assertEqual(build.call_count, 1)
            self.client.get(reverse("icarus_analytics"), {"refresh": "1"})
            self.assertEqual(build.call_count, 2)


class DashboardPanelAccessTests(WagtailPageTestCase):
    """Readership panels are analytics; the audit panel used to crash on a nameless actor."""

    def _dashboard(self, user):
        from django.urls import reverse

        self.client.force_login(user)
        response = self.client.get(reverse("wagtailadmin_home"))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_panels_hidden_from_editors_without_analytics_access(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission

        editor = get_user_model().objects.create_user(phone_number="+918800780001", name="Editor", is_staff=True)
        editor.user_permissions.add(Permission.objects.get(codename="access_admin"))
        html = self._dashboard(editor)
        self.assertNotIn("Audio Coverage", html)
        self.assertNotIn("Recent Activity", html)

    def test_superuser_sees_them_even_with_a_nameless_actor(self):
        from auditlog import get_logentry_model
        from django.contrib.auth import get_user_model
        from django.contrib.contenttypes.models import ContentType

        User = get_user_model()
        root = User.objects.create_superuser(phone_number="+918800780002", name="Root", password="x")
        nameless = User.objects.create_user(phone_number="+918800780003", name="N")
        User.objects.filter(pk=nameless.pk).update(name="")
        get_logentry_model().objects.create(
            content_type=ContentType.objects.get_for_model(User), object_pk="1", object_id=1,
            object_repr="x", action=1, changes={}, actor=User.objects.get(pk=nameless.pk),
        )
        html = self._dashboard(root)
        self.assertIn("Audio Coverage", html)
        self.assertIn(f"Reader #{nameless.pk}", html)


class PageMarkupTests(WagtailPageTestCase):
    """base.html: doctype first, real lang attribute, hreflang alternates, SRI."""

    def setUp(self):
        root = Page.get_first_root_node()
        Site.objects.all().delete()
        for child in root.get_children():
            child.delete()
        root = Page.get_first_root_node()
        self.home = HomePage(title="Home", slug="home")
        root.add_child(instance=self.home)
        Site.objects.create(hostname="testserver", root_page=self.home, is_default_site=True)

    def test_doctype_comes_first_and_lang_follows_the_url(self):
        ml = self.client.get("/").content.decode()
        en = self.client.get("/en/").content.decode()
        # Anything before the doctype (it used to be a favicon <link>) puts
        # the page into quirks mode.
        self.assertTrue(ml.lstrip().startswith("<!DOCTYPE html>"))
        self.assertIn('<html lang="ml">', ml)
        self.assertIn('<html lang="en">', en)
        self.assertIn('<meta property="og:locale" content="en_IN">', en)

    def test_language_alternates_are_linked(self):
        html = self.client.get("/").content.decode()
        self.assertIn('<link rel="alternate" hreflang="ml" href="http://testserver/">', html)
        self.assertIn('<link rel="alternate" hreflang="en" href="http://testserver/en/">', html)

    def test_cdn_assets_carry_integrity_hashes(self):
        html = self.client.get("/").content.decode()
        self.assertRegex(html, r'font-awesome/6\.0\.0/css/all\.min\.css" integrity="sha512-[^"]+" crossorigin="anonymous"')


class AdminHygieneTests(WagtailPageTestCase):
    def test_audit_log_menu_goes_to_the_one_real_view(self):
        from django.urls import NoReverseMatch, reverse

        from reader.wagtail_hooks import register_auditlog_menu_item

        self.assertEqual(register_auditlog_menu_item().url, reverse("icarus_audit_log"))
        with self.assertRaises(NoReverseMatch):
            reverse("auditlog_view")  # the unreachable duplicate is gone

    def test_admin_toast_script_is_cacheable(self):
        from reader.wagtail_hooks import global_admin_js

        self.assertNotIn("?v=", str(global_admin_js()))
