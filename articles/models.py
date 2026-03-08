from django.db import models
from django import forms
from modelcluster.fields import ParentalKey
from wagtail.models import Page
from wagtail.fields import RichTextField, StreamField
from wagtail.admin.panels import FieldPanel, InlinePanel, MultiFieldPanel
from wagtail.admin.forms import WagtailAdminPageForm
from .wagtail_hooks import CoverImagePreviewPanel
from wagtail import blocks
from wagtail.images.blocks import ImageChooserBlock
from wagtail.embeds.blocks import EmbedBlock
from wagtail.documents.blocks import DocumentChooserBlock
from wagtail.contrib.table_block.blocks import TableBlock
from wagtail.contrib.settings.models import BaseSiteSetting, register_setting
from .wagtail_widgets import ColorPickerBlock

class ColoredHeadingBlock(blocks.StructBlock):
    text = blocks.CharBlock()
    heading_level = blocks.ChoiceBlock(choices=[
        ('h2', 'Heading 2'),
        ('h3', 'Heading 3'),
        ('h4', 'Heading 4'),
    ], default='h2')
    text_color = ColorPickerBlock(required=False, default="#000000", help_text="Pick a color for the heading text (e.g. #FF0000).")

    class Meta:
        icon = 'title'
        template = 'blocks/colored_heading_block.html'
        label = 'Colored Heading'


class BlockQuoteBlock(blocks.StructBlock):
    text = blocks.RichTextBlock()
    attribute_name = blocks.CharBlock(
        required=False,
        label='e.g. Mary Berry',
        help_text="The person to attribute the quote to"
    )

    class Meta:
        icon = 'openquote'
        label = 'Blockquote'
        template = 'blocks/blockquote_block.html'


class ImageBlock(blocks.StructBlock):
    """Image with optional caption and text-wrap alignment."""
    image = ImageChooserBlock()
    caption = blocks.RichTextBlock(
        required=False,
        label='Caption',
        help_text="Optional caption displayed below the image"
    )
    alignment = blocks.ChoiceBlock(
        choices=[
            ('full',  'Full width - image fills the entire text column, no wrap'),
            ('left',  'Float left - image sits left, text flows around the right side'),
            ('right', 'Float right - image sits right, text flows around the left side'),
        ],
        default='full',
        label='Image placement',
        help_text="Choose how the image sits relative to the surrounding text"
    )

    class Meta:
        icon = 'image'
        label = 'Image'


# ── Reusable StreamField Blocks ─────────────────────────────────────────

STREAM_BLOCKS = [
    ('heading',    blocks.RichTextBlock(form_classname="full title")),
    ('colored_heading', ColoredHeadingBlock(label="Colored Heading")),
    ('paragraph',  blocks.RichTextBlock()),
    ('image',      ImageBlock()),
    ('blockquote', BlockQuoteBlock()),
    ('embed',      EmbedBlock(help_text="Insert a URL to embed (e.g. YouTube, Vimeo, SoundCloud)")),
    ('document',   DocumentChooserBlock()),
    ('table',      TableBlock()),
    ('raw_html',   blocks.RawHTMLBlock(help_text="Use with caution: raw HTML is not sanitised")),
    ('text',       blocks.TextBlock()),
    ('email',      blocks.EmailBlock()),
    ('url',        blocks.URLBlock()),
    ('boolean',    blocks.BooleanBlock(required=False)),
    ('integer',    blocks.IntegerBlock()),
    ('float',      blocks.FloatBlock()),
    ('decimal',    blocks.DecimalBlock()),
    ('date',       blocks.DateBlock()),
    ('time',       blocks.TimeBlock()),
    ('datetime',   blocks.DateTimeBlock()),
    ('rich_text',  blocks.RichTextBlock(label="Rich Text (Full)")),
    ('choice',     blocks.ChoiceBlock(choices=[
        ('left',   'Left'),
        ('center', 'Centre'),
        ('right',  'Right'),
    ], help_text="Alignment choice")),
    ('page',       blocks.PageChooserBlock()),
    ('static',     blocks.StaticBlock(
        admin_text="This is a placeholder block — content is defined in the template.",
        label="Divider / Separator",
    )),
    ('list',       blocks.ListBlock(blocks.CharBlock(), label="List of items")),
    ('stream',     blocks.StreamBlock([
        ('text',  blocks.CharBlock()),
        ('image', ImageChooserBlock()),
    ], label="Nested Stream")),
]

class ArticleForm(WagtailAdminPageForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            # We use apps.get_model to avoid circular imports
            from django.apps import apps
            Issue = apps.get_model('issue', 'Issue')
            latest_issue = Issue.objects.live().order_by('-date_of_publishing').first()
            if latest_issue:
                self.fields['main_issue'].queryset = Issue.objects.filter(pk=latest_issue.pk)
                self.fields['main_issue'].initial = latest_issue.pk
                self.initial['main_issue'] = latest_issue.pk
                self.fields['main_issue'].widget = forms.Select(choices=self.fields['main_issue'].choices)
                self.fields['main_issue'].required = False


class Article(Page):
    base_form_class = ArticleForm

    parent_page_types = ['ArticleIndexPage']

    # ── Relations ──────────────────────────────────────────────────────────
    main_issue = models.ForeignKey(
        'issue.Issue',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='primary_articles'
    )

    topic = models.ForeignKey(
        'issue.Topic',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='articles'
    )

    # ── Metadata ───────────────────────────────────────────────────────────
    date = models.DateField("Date of publishing")

    # ── Cover image ────────────────────────────────────────────────────────
    cover_image = models.ForeignKey(
        'wagtailimages.Image',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )

    COVER_ASPECT_CHOICES = [
        ('3x1',  'Thin strip - very wide and short (good for landscapes & skylines)'),
        ('2x1',  'Wide banner - wide and cinematic (works well for most photos)'),
        ('16x9', 'Widescreen - standard TV shape (great for screenshots & events)'),
        ('4x3',  'Classic photo - everyday camera shape (suits portraits & objects)'),
        ('1x1',  'Square - equal width and height (good for faces & close-ups)'),
    ]

    cover_image_aspect = models.CharField(
        verbose_name="Image shape",
        max_length=10,
        choices=COVER_ASPECT_CHOICES,
        default='2x1',
        help_text=(
            "Choose how the cover photo is cropped at the top of the article. "
            "If part of the photo gets cut off, try a different shape - or open the image "
            "in the image library and set a focal point to control which part stays in frame."
        )
    )

    cover_image_caption = models.CharField(
        verbose_name="Photo caption / credit",
        max_length=500,
        blank=True,
        help_text="Short description or credit shown beneath the cover photo. e.g. 'Photo: Jane Smith / Reuters'"
    )

    # ── Body (Default) ─────────────────────────────────────────────────────
    body = StreamField(STREAM_BLOCKS, use_json_field=True, null=True, blank=True)

    # ── Translations (Optional) ────────────────────────────────────────────
    title_en = models.CharField(max_length=255, blank=True, verbose_name="Title (English)")
    body_en = StreamField(STREAM_BLOCKS, use_json_field=True, null=True, blank=True, verbose_name="Body (English)")

    title_hi = models.CharField(max_length=255, blank=True, verbose_name="Title (Hindi)")
    body_hi = StreamField(STREAM_BLOCKS, use_json_field=True, null=True, blank=True, verbose_name="Body (Hindi)")

    title_ta = models.CharField(max_length=255, blank=True, verbose_name="Title (Tamil)")
    body_ta = StreamField(STREAM_BLOCKS, use_json_field=True, null=True, blank=True, verbose_name="Body (Tamil)")

    # ── Admin panels ───────────────────────────────────────────────────────
    content_panels = Page.content_panels + [
        FieldPanel('main_issue'),
        FieldPanel('date'),
        FieldPanel('topic'),
        MultiFieldPanel([
            FieldPanel('cover_image'),
            FieldPanel('cover_image_aspect'),
            FieldPanel('cover_image_caption'),
            CoverImagePreviewPanel(),
        ], heading="Cover Image"),
        InlinePanel('article_authors', label="Authors"),
        FieldPanel('body'),
        MultiFieldPanel([
            FieldPanel('title_en'),
            FieldPanel('body_en'),
            FieldPanel('title_hi'),
            FieldPanel('body_hi'),
            FieldPanel('title_ta'),
            FieldPanel('body_ta'),
        ], heading="Translations (Optional)", classname="collapsible collapsed"),
    ]

    # ── Helpers ────────────────────────────────────────────────────────────
    @property
    def cover_fill_spec(self):
        """
        Returns a Wagtail image fill spec string for the chosen aspect ratio,
        scaled to a fixed width of 1200px so the template stays simple.
        """
        specs = {
            '3x1':  'fill-1200x400',
            '2x1':  'fill-1200x600',
            '16x9': 'fill-1200x675',
            '4x3':  'fill-1200x900',
            '1x1':  'fill-1200x1200',
        }
        return specs.get(self.cover_image_aspect, 'fill-1200x600')

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        from django.conf import settings as django_settings

        # Query Article siblings directly (not the base Page queryset) so that
        # topic filtering and specific fields are available without extra joins.
        siblings = (
            Article.objects
            .live()
            .not_page(self)
            .child_of(self.get_parent())
            .order_by('-first_published_at')
        )

        # Prefer articles in the same topic if there are enough of them
        if self.topic_id:
            same_topic = siblings.filter(topic=self.topic_id)
            if same_topic.count() >= 3:
                siblings = same_topic

        context['related_articles'] = siblings[:3]

        # ── Paywall Logic ─────────────────────────────────────────────
        free_limit = getattr(django_settings, 'FREE_ARTICLE_LIMIT', 3)
        show_paywall = False
        truncated_body = None
        reader = None

        # 1. Admin Exemption
        if request.user.is_superuser or request.user.is_staff:
            show_paywall = False
        
        # 2. Authenticated Reader Logic
        elif request.user.is_authenticated:
            try:
                reader = request.user.reader
            except Exception:
                reader = None

            if reader and reader.is_subscribed:
                # Subscribed reader → full access, track the read
                show_paywall = False
                reader.read_articles.add(self)
            elif reader:
                # Logged-in but not subscribed
                if reader.read_articles.filter(pk=self.pk).exists():
                    show_paywall = False
                else:
                    read_count = reader.read_articles.count()
                    if read_count < free_limit:
                        show_paywall = False
                        reader.read_articles.add(self)
                    else:
                        show_paywall = True
            else:
                # Authenticated but no reader profile (shouldn't happen with signals but as fallback)
                show_paywall = True
        
        # 3. Anonymous User Logic (Session-based tracking)
        else:
            free_reads = request.session.get('free_reads', [])
            if self.pk in free_reads:
                show_paywall = False
            else:
                if len(free_reads) < free_limit:
                    show_paywall = False
                    free_reads.append(self.pk)
                    request.session['free_reads'] = free_reads
                else:
                    show_paywall = True

        if show_paywall:
            # Provide only the first 2 body blocks for the truncated preview
            truncated_body = list(self.body)[:2]

        context['show_paywall'] = show_paywall
        context['truncated_body'] = truncated_body
        context['reader'] = reader
        return context

    @property
    def excerpt(self):
        for block in self.body:
            if block.block_type in ['paragraph', 'text', 'rich_text']:
                return block.value
        return ""

class ArticleIndexPage(Page):
    intro = RichTextField(blank=True)

    max_count = 1

    content_panels = Page.content_panels + [
        FieldPanel('intro')
    ]

    subpage_types = ['Article']

    def get_context(self, request):
        context = super().get_context(request)
        all_articles = self.get_children().live().order_by('-first_published_at')
        context['articles'] = all_articles
        return context