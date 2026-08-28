from home.models import HomePage

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
