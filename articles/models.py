from django.db import models
from django import forms
from modelcluster.fields import ParentalKey
from modelcluster.contrib.taggit import ClusterTaggableManager
from taggit.models import TaggedItemBase
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

from hitcount.models import HitCountMixin, HitCount
from django.contrib.contenttypes.fields import GenericRelation
from hitcount.views import HitCountMixin as HitCountViewMixin

class ArticleTag(TaggedItemBase):
    content_object = ParentalKey(
        'Article',
        related_name='tagged_items',
        on_delete=models.CASCADE
    )

class ArticleIndexPageTag(TaggedItemBase):
    content_object = ParentalKey(
        'ArticleIndexPage',
        related_name='tagged_items',
        on_delete=models.CASCADE
    )

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


class AudioBlock(blocks.StructBlock):
    audio_file = DocumentChooserBlock(required=False, help_text="Upload an audio file (mp3, wav, etc.)")
    audio_embed_url = blocks.URLBlock(required=False, help_text="Or paste an embed link (SoundCloud, etc.)")
    caption = blocks.CharBlock(required=False, help_text="Optional caption for the audio")

    class Meta:
        icon = 'media'
        template = 'blocks/audio_block.html'
        label = 'Audio'


class VideoBlock(blocks.StructBlock):
    video_file = DocumentChooserBlock(required=False, help_text="Upload a video file (mp4, webm, etc.)")
    video_embed_url = blocks.URLBlock(required=False, help_text="Or paste an embed link (YouTube, Vimeo, etc.)")
    caption = blocks.CharBlock(required=False, help_text="Optional caption for the video")

    class Meta:
        icon = 'media'
        template = 'blocks/video_block.html'
        label = 'Video'


class CalloutBlock(blocks.StructBlock):
    """Highlighted callout box — ideal for tips, warnings, or key takeaways."""
    callout_type = blocks.ChoiceBlock(
        choices=[
            ('info',    'ℹ Info — general note or context'),
            ('tip',     '💡 Tip — helpful suggestion'),
            ('warning', '⚠ Warning — caution or important caveat'),
            ('danger',  '🚨 Danger — critical alert'),
            ('success', '✅ Success — positive outcome or confirmation'),
            ('quote',   '💬 Highlight — pull-attention excerpt'),
        ],
        default='info',
        label='Box style',
    )
    title = blocks.CharBlock(required=False, help_text="Optional bold heading inside the box")
    content = blocks.RichTextBlock(help_text="Body text of the callout")

    class Meta:
        icon = 'warning'
        template = 'blocks/callout_block.html'
        label = 'Callout Box'


class PullQuoteBlock(blocks.StructBlock):
    """Large, typographically prominent pull-quote for visual emphasis mid-article."""
    quote = blocks.CharBlock(
        label='Quote text',
        help_text="The highlighted sentence or phrase — keep it short and punchy"
    )
    attribution = blocks.CharBlock(
        required=False,
        label='Attribution',
        help_text="Who said it (optional)"
    )
    alignment = blocks.ChoiceBlock(
        choices=[
            ('left',   'Inset left — floats to the left, text wraps right'),
            ('center', 'Centred — full-width, visually dominant'),
            ('right',  'Inset right — floats to the right, text wraps left'),
        ],
        default='center',
    )

    class Meta:
        icon = 'openquote'
        template = 'blocks/pull_quote_block.html'
        label = 'Pull Quote'


class CodeBlock(blocks.StructBlock):
    """Syntax-highlighted code snippet with language label."""
    language = blocks.ChoiceBlock(
        choices=[
            ('python',     'Python'),
            ('javascript', 'JavaScript'),
            ('typescript', 'TypeScript'),
            ('html',       'HTML'),
            ('css',        'CSS'),
            ('bash',       'Bash / Shell'),
            ('sql',        'SQL'),
            ('json',       'JSON'),
            ('yaml',       'YAML'),
            ('rust',       'Rust'),
            ('go',         'Go'),
            ('java',       'Java'),
            ('c',          'C'),
            ('cpp',        'C++'),
            ('plaintext',  'Plain text (no highlighting)'),
        ],
        default='python',
    )
    code = blocks.TextBlock(
        label='Code',
        help_text="Paste your code here — indentation is preserved"
    )
    caption = blocks.CharBlock(
        required=False,
        help_text="Optional note shown below the code block (e.g. filename or source)"
    )

    class Meta:
        icon = 'code'
        template = 'blocks/code_block.html'
        label = 'Code Snippet'


class ButtonBlock(blocks.StructBlock):
    """A call-to-action button — link anywhere, styled to match context."""
    label = blocks.CharBlock(help_text="Button text, e.g. 'Read more' or 'Subscribe'")
    url = blocks.URLBlock(help_text="Destination URL")
    style = blocks.ChoiceBlock(
        choices=[
            ('primary',   'Primary — solid filled, most prominent'),
            ('secondary', 'Secondary — outlined, less prominent'),
            ('ghost',     'Ghost — minimal, transparent background'),
            ('danger',    'Danger — red, for destructive or urgent actions'),
        ],
        default='primary',
    )
    open_in_new_tab = blocks.BooleanBlock(
        required=False,
        default=False,
        label='Open in new tab',
    )
    alignment = blocks.ChoiceBlock(
        choices=[
            ('left',   'Left'),
            ('center', 'Centre'),
            ('right',  'Right'),
        ],
        default='left',
    )

    class Meta:
        icon = 'link'
        template = 'blocks/button_block.html'
        label = 'Button / CTA'


class ImageGalleryBlock(blocks.StructBlock):
    """A responsive image grid — multiple photos displayed together."""
    images = blocks.ListBlock(
        ImageChooserBlock(),
        label='Images',
        help_text="Add two or more images to display as a gallery"
    )
    layout = blocks.ChoiceBlock(
        choices=[
            ('2col', '2 columns — side-by-side pairs'),
            ('3col', '3 columns — compact grid'),
            ('4col', '4 columns — dense mosaic'),
            ('masonry', 'Masonry — Pinterest-style variable heights'),
        ],
        default='3col',
        label='Grid layout',
    )
    caption = blocks.CharBlock(required=False, help_text="Optional caption for the whole gallery")

    class Meta:
        icon = 'image'
        template = 'blocks/image_gallery_block.html'
        label = 'Image Gallery'


class TwoColumnBlock(blocks.StructBlock):
    """Side-by-side two-column layout — rich text on each side."""
    left_column = blocks.RichTextBlock(label='Left column')
    right_column = blocks.RichTextBlock(label='Right column')
    split = blocks.ChoiceBlock(
        choices=[
            ('50-50', 'Equal — 50 / 50'),
            ('60-40', 'Left-heavy — 60 / 40'),
            ('40-60', 'Right-heavy — 40 / 60'),
            ('70-30', 'Left-dominant — 70 / 30'),
            ('30-70', 'Right-dominant — 30 / 70'),
        ],
        default='50-50',
        label='Column split',
    )

    class Meta:
        icon = 'grip'
        template = 'blocks/two_column_block.html'
        label = 'Two Columns'


class AccordionItemBlock(blocks.StructBlock):
    """A single collapsible accordion row."""
    heading = blocks.CharBlock(label='Question / heading')
    content = blocks.RichTextBlock(label='Answer / body')

    class Meta:
        icon = 'collapse-down'
        label = 'Accordion item'


class AccordionBlock(blocks.StructBlock):
    """A set of collapsible FAQ-style accordion sections."""
    title = blocks.CharBlock(
        required=False,
        help_text="Optional heading above the accordion, e.g. 'Frequently asked questions'"
    )
    items = blocks.ListBlock(AccordionItemBlock(), label='Items')
    allow_multiple_open = blocks.BooleanBlock(
        required=False,
        default=False,
        label='Allow multiple sections open at once',
    )

    class Meta:
        icon = 'list-ul'
        template = 'blocks/accordion_block.html'
        label = 'Accordion / FAQ'


class TimelineItemBlock(blocks.StructBlock):
    """One entry in a chronological timeline."""
    date = blocks.CharBlock(
        label='Date / period',
        help_text="e.g. 'March 2024', '1947', or 'Day 3'"
    )
    heading = blocks.CharBlock(label='Event heading')
    description = blocks.RichTextBlock(required=False, label='Details')
    image = ImageChooserBlock(required=False, label='Optional image')

    class Meta:
        icon = 'time'
        label = 'Timeline entry'


class TimelineBlock(blocks.StructBlock):
    """A vertical chronological timeline — good for history, biography, or project milestones."""
    title = blocks.CharBlock(required=False, help_text="Optional heading above the timeline")
    entries = blocks.ListBlock(TimelineItemBlock(), label='Timeline entries')
    direction = blocks.ChoiceBlock(
        choices=[
            ('vertical',    'Vertical — stacked top-to-bottom'),
            ('alternating', 'Alternating — entries flip left/right'),
        ],
        default='vertical',
    )

    class Meta:
        icon = 'time'
        template = 'blocks/timeline_block.html'
        label = 'Timeline'


class StatBlock(blocks.StructBlock):
    """A bold, eye-catching statistic — number, label, and optional context."""
    statistic = blocks.CharBlock(
        label='Figure',
        help_text="The number or value, e.g. '₹4.2 Cr', '73%', '1 in 5'"
    )
    label = blocks.CharBlock(
        label='Label',
        help_text="What the number represents, e.g. 'displaced families'"
    )
    context = blocks.CharBlock(
        required=False,
        label='Context note',
        help_text="Small supporting note, e.g. 'Source: UNHCR 2024'"
    )
    highlight_color = ColorPickerBlock(
        required=False,
        default="#000000",
        help_text="Accent colour for the figure"
    )

    class Meta:
        icon = 'order'
        template = 'blocks/stat_block.html'
        label = 'Statistic Highlight'


class TestimonialBlock(blocks.StructBlock):
    """A person's testimonial or endorsement — quote, name, role, and optional photo."""
    quote = blocks.RichTextBlock(
        label='Quote',
        help_text="What the person said"
    )
    name = blocks.CharBlock(label='Name')
    role = blocks.CharBlock(
        required=False,
        label='Title / role',
        help_text="e.g. 'District Collector, Wayanad'"
    )
    photo = ImageChooserBlock(required=False, label='Photo')
    style = blocks.ChoiceBlock(
        choices=[
            ('card',    'Card — framed box with shadow'),
            ('inline',  'Inline — minimal, runs with the body text'),
            ('feature', 'Feature — large, full-bleed highlight'),
        ],
        default='card',
    )

    class Meta:
        icon = 'group'
        template = 'blocks/testimonial_block.html'
        label = 'Testimonial'


class ChapterBreakBlock(blocks.StructBlock):
    """A visual section divider — optionally labelled, to break long articles into chapters."""
    chapter_label = blocks.CharBlock(
        required=False,
        label='Chapter label',
        help_text="Optional short label above the title, e.g. 'Part II' or 'Chapter 3'"
    )
    title = blocks.CharBlock(
        required=False,
        label='Chapter title',
        help_text="Optional heading for the new section"
    )
    style = blocks.ChoiceBlock(
        choices=[
            ('line',      'Line — a simple horizontal rule'),
            ('spaced',    'Spaced — extra white space, no decoration'),
            ('ornamental','Ornamental — decorative divider symbol'),
            ('numbered',  'Numbered — auto-increments chapter number'),
        ],
        default='line',
    )

    class Meta:
        icon = 'horizontalrule'
        template = 'blocks/chapter_break_block.html'
        label = 'Chapter Break'


# ── Reusable StreamField Blocks ─────────────────────────────────────────

STREAM_BLOCKS = [

    # ── Text & Headings ───────────────────────────────────────────────────
    ('heading',         blocks.RichTextBlock(
                            form_classname="full title",
                            label="Heading (rich text)",
                            help_text="A plain rich-text heading"
                        )),
    ('colored_heading', ColoredHeadingBlock()),
    ('paragraph',       blocks.RichTextBlock(label="Paragraph")),
    ('rich_text',       blocks.RichTextBlock(label="Rich Text (Full)")),
    ('text',            blocks.TextBlock(label="Plain Text")),
    ('pull_quote',      PullQuoteBlock()),
    ('blockquote',      BlockQuoteBlock()),
    ('code',            CodeBlock()),
    ('chapter_break',   ChapterBreakBlock()),

    # ── Media ─────────────────────────────────────────────────────────────
    ('image',           ImageBlock()),
    ('image_gallery',   ImageGalleryBlock()),
    ('audio',           AudioBlock()),
    ('video',           VideoBlock()),
    ('embed',           EmbedBlock(
                            help_text="Insert a URL to embed (e.g. YouTube, Vimeo, SoundCloud, Twitter/X)"
                        )),
    ('document',        DocumentChooserBlock(
                            help_text="Attach a downloadable file (PDF, Word doc, etc.)"
                        )),

    # ── Layout & Structure ────────────────────────────────────────────────
    ('two_columns',     TwoColumnBlock()),
    ('table',           TableBlock()),
    ('accordion',       AccordionBlock()),
    ('timeline',        TimelineBlock()),
    ('static',          blocks.StaticBlock(
                            admin_text="Divider / Separator — renders a horizontal rule in the template.",
                            label="Divider / Separator",
                        )),

    # ── Callouts & Highlights ─────────────────────────────────────────────
    ('callout',         CalloutBlock()),
    ('stat',            StatBlock()),
    ('testimonial',     TestimonialBlock()),
    ('button',          ButtonBlock()),

    # ── Choosers ──────────────────────────────────────────────────────────
    ('page',            blocks.PageChooserBlock(
                            help_text="Link to another page on this site"
                        )),

    # ── Raw / Advanced ────────────────────────────────────────────────────
    ('raw_html',        blocks.RawHTMLBlock(
                            help_text="⚠ Use with caution: raw HTML is not sanitised"
                        )),

    # ── Primitive / Data ──────────────────────────────────────────────────
    ('email',           blocks.EmailBlock()),
    ('url',             blocks.URLBlock()),
    ('integer',         blocks.IntegerBlock()),
    ('float',           blocks.FloatBlock()),
    ('decimal',         blocks.DecimalBlock()),
    ('boolean',         blocks.BooleanBlock(required=False)),
    ('date',            blocks.DateBlock()),
    ('time',            blocks.TimeBlock()),
    ('datetime',        blocks.DateTimeBlock()),
    ('choice',          blocks.ChoiceBlock(choices=[
                            ('left',   'Left'),
                            ('center', 'Centre'),
                            ('right',  'Right'),
                        ], help_text="Alignment choice")),
    ('list',            blocks.ListBlock(
                            blocks.CharBlock(),
                            label="List of items"
                        )),
    ('stream',          blocks.StreamBlock([
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
                # Set the initial default value to the latest issue
                self.fields['main_issue'].initial = latest_issue.pk
                self.initial['main_issue'] = latest_issue.pk
                

class Article(Page, HitCountMixin):
    base_form_class = ArticleForm

    hit_count_generic = GenericRelation(
        HitCount, object_id_field='object_pk',
        related_query_name='hit_count_generic_relation'
    )

    tags = ClusterTaggableManager(through=ArticleTag, blank=True)

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

    # ── Audio ─────────────────────────────────────────────────────────────
    audio_file = models.ForeignKey(
        'wagtaildocs.Document',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+'
    )
    audio_embed_url = models.URLField(
        blank=True,
        null=True,
        help_text="Paste an embed link for audio (e.g., SoundCloud, Spotify)"
    )

    # ── Video ─────────────────────────────────────────────────────────────
    video_file = models.ForeignKey(
        'wagtaildocs.Document',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+'
    )
    video_embed_url = models.URLField(
        blank=True,
        null=True,
        help_text="Paste an embed link for video (e.g., YouTube, Vimeo)"
    )

    # ── Analytics ─────────────────────────────────────────────────────────
    read_fully_count = models.PositiveIntegerField(default=0, editable=False)
    # ── Admin panels ───────────────────────────────────────────────────────
    content_panels = Page.content_panels + [
        FieldPanel('slug'),
        FieldPanel('tags'),
        FieldPanel('main_issue'),
        FieldPanel('date'),
        FieldPanel('topic'),
        MultiFieldPanel([
            FieldPanel('cover_image'),
            FieldPanel('cover_image_aspect'),
            FieldPanel('cover_image_caption'),
            CoverImagePreviewPanel(),
        ], heading="Cover Image"),
        MultiFieldPanel([
            FieldPanel('audio_file'),
            FieldPanel('audio_embed_url'),
        ], heading="Audio (Main)"),
        MultiFieldPanel([
            FieldPanel('video_file'),
            FieldPanel('video_embed_url'),
        ], heading="Video (Main)"),
        InlinePanel('article_authors', label="Authors"),
        FieldPanel('body'),
        MultiFieldPanel([
            FieldPanel('read_fully_count', read_only=True),
        ], heading="Analytics"),
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

        # ── Analytics: Increment Opened Count ──────────────────────────
        if not (request.user.is_superuser or request.user.is_staff):
            hit_count = HitCount.objects.get_for_object(self)
            HitCountViewMixin().hit_count(request, hit_count)

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

        # 1. Reader Profile Fetch
        if request.user.is_authenticated:
            reader = request.user

        # 2. Admin Exemption
        if request.user.is_superuser or request.user.is_staff:
            show_paywall = False
        
        # 3. Authenticated Reader Logic
        elif request.user.is_authenticated:

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
    tags = ClusterTaggableManager(through=ArticleIndexPageTag, blank=True)

    max_count = 1

    content_panels = Page.content_panels + [
        FieldPanel('intro'),
        FieldPanel('tags'),
    ]

    subpage_types = ['Article']

    def get_context(self, request):
        context = super().get_context(request)
        all_articles = self.get_children().live().order_by('-first_published_at')
        context['articles'] = all_articles
        return context