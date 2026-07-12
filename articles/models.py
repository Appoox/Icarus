from django.db import models
from django import forms
from django.utils.functional import cached_property
from django.http import HttpResponse
from django.template.loader import render_to_string
from wagtail.contrib.routable_page.models import RoutablePageMixin, route
from weasyprint import HTML
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
from django.shortcuts import redirect

import json
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

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
    alt_text = blocks.CharBlock(
        required=False,
        label='Alt text',
        help_text="For screen readers. Leave blank to use the image's own title."
    )
    caption = blocks.RichTextBlock(
        required=False,
        label='Caption',
        help_text="Optional caption displayed below the image"
    )
    credit = blocks.CharBlock(
        required=False,
        label='Credit',
        help_text="Photo credit, e.g. 'ISRO' or a photographer's name"
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
    size = blocks.ChoiceBlock(
        choices=[
            ('small', 'Small (340px)'),
            ('medium', 'Medium (680px)'),
            ('large', 'Large (1000px)'),
        ],
        default='medium',
        label='Image Size'
    )

    class Meta:
        icon = 'image'
        label = 'Image'
        template = 'blocks/image_block.html'


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

class RichTextImportBlock(blocks.TextBlock):
    """
    Paste raw HTML or plain text into this block in the Wagtail editor.

    Clicking "Convert to Blocks" in the editor toolbar adds a hidden
    `convert_block_id` field to the Save Draft form submission.
    ArticleForm.save() detects that field, converts this block's content
    into proper heading / paragraph / blockquote / image blocks,
    and the page reloads with the expanded blocks ready to edit.

    This block should never reach the live site — the conversion always
    happens before the page is saved. The template is a safety fallback only.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('rows', 8)
        super().__init__(*args, **kwargs)

    @cached_property
    def field(self):
        # Override the default plain <textarea> with one that captures the
        # clipboard's text/html payload on paste (via rich_text_import_paste.js),
        # so headings / paragraphs / images survive into the converter instead
        # of being flattened to text/plain.
        from .wagtail_widgets import RichTextImportTextarea
        field_kwargs = {'widget': RichTextImportTextarea(attrs={'rows': self.rows})}
        field_kwargs.update(self.field_options)
        return forms.CharField(**field_kwargs)

    class Meta:
        icon      = 'clipboard-list'
        label     = 'Paste & Import'
        help_text = (
            'Paste HTML or plain text here, then click "Convert to Blocks" '
            'in the toolbar above the text area.'
        )
        template  = 'blocks/rich_text_import_block.html'


# ── Science / editorial content blocks ──────────────────────────────────

class CalloutBlock(blocks.StructBlock):
    """Info / note / tip / warning / key-point box — good for asides."""
    style = blocks.ChoiceBlock(choices=[
        ('info', 'Info'),
        ('note', 'Note'),
        ('tip', 'Tip'),
        ('warning', 'Warning'),
        ('key', 'Key point'),
    ], default='info')
    title = blocks.CharBlock(required=False)
    text  = blocks.RichTextBlock()

    class Meta:
        icon = 'help'
        label = 'Callout / Box'
        template = 'blocks/callout_block.html'


class MathBlock(blocks.StructBlock):
    """LaTeX equation — rendered client-side by KaTeX auto-render."""
    latex   = blocks.TextBlock(help_text=r"LaTeX without delimiters, e.g. E = mc^2")
    display = blocks.BooleanBlock(
        required=False, default=True,
        help_text="On = centred block equation; off = inline."
    )

    class Meta:
        icon = 'cog'
        label = 'Equation (LaTeX)'
        template = 'blocks/math_block.html'


class ReferencesBlock(blocks.StructBlock):
    """Numbered citations / footnotes."""
    heading = blocks.CharBlock(required=False, default="References")
    items   = blocks.ListBlock(
        blocks.RichTextBlock(features=['bold', 'italic', 'link'])
    )

    class Meta:
        icon = 'list-ol'
        label = 'References / Footnotes'
        template = 'blocks/references_block.html'


class CodeBlock(blocks.StructBlock):
    """Syntax-highlighted code — only needed if you publish code."""
    language = blocks.ChoiceBlock(choices=[
        ('python', 'Python'),
        ('javascript', 'JavaScript'),
        ('bash', 'Bash'),
        ('html', 'HTML'),
        ('css', 'CSS'),
        ('json', 'JSON'),
        ('sql', 'SQL'),
        ('text', 'Plain'),
    ], default='python')
    code = blocks.TextBlock()

    class Meta:
        icon = 'code'
        label = 'Code'
        template = 'blocks/code_block.html'



# ── Reusable StreamField Blocks ─────────────────────────────────────────

STREAM_BLOCKS = [
    # ── Import helper (auto-expands on save) ───────────────────────────────
    ('rich_text_import', RichTextImportBlock()),

    # ── Text ───────────────────────────────────────────────────────────────
    ('heading',         blocks.RichTextBlock(form_classname="full title")),
    ('colored_heading', ColoredHeadingBlock(label="Colored Heading")),
    ('paragraph',       blocks.RichTextBlock()),
    ('blockquote',      BlockQuoteBlock()),
    ('callout',         CalloutBlock()),
    ('references',      ReferencesBlock()),

    # ── Media & Embeds ─────────────────────────────────────────────────────
    ('image',           ImageBlock()),
    ('audio',           AudioBlock()),
    ('video',           VideoBlock()),
    ('embed',           EmbedBlock(help_text="Insert a URL to embed (e.g. YouTube, Vimeo, SoundCloud)")),
    ('document',        DocumentChooserBlock()),
    ('table',           TableBlock()),

    # ── Science ────────────────────────────────────────────────────────────
    ('math',            MathBlock()),
    ('code',            CodeBlock()),   # optional — remove if you never publish code

    # ── Structure & escape hatch ───────────────────────────────────────────
    ('divider',         blocks.StaticBlock(
        admin_text='A horizontal section divider.',
        label='Divider',
        template='blocks/divider_block.html',
    )),
    ('raw_html',        blocks.RawHTMLBlock(help_text="Raw HTML — not sanitised, use with care.")),
]

class ArticleForm(WagtailAdminPageForm):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            from django.apps import apps
            Issue = apps.get_model('issue', 'Issue')
            latest_issue = Issue.objects.live().order_by('-date_of_publishing').first()
            if latest_issue:
                self.fields['main_issue'].initial = latest_issue.pk
                self.initial['main_issue'] = latest_issue.pk

    def save(self, commit=True):
        """
        Override save() to convert rich_text_import blocks before saving.

        Two modes:
        1. Targeted — JS sets convert_block_id=<uuid> on the form when the
           editor clicks "Convert to Blocks". Only that block is converted.
           If convert_block_id='__all__', all import blocks are converted
           (used as a JS fallback when the block UUID can't be determined).

        2. Auto-fallback — if no convert_block_id is present in POST at all,
           any rich_text_import blocks are still converted automatically.
           This ensures conversion always happens even if the JS injection
           fails entirely.
        """
        page = super().save(commit=False)

        convert_block_id = (self.data.get('convert_block_id') or '').strip()

        if convert_block_id == '__all__' or not convert_block_id:
            # Convert every import block on the page
            self._convert_all_import_blocks(page)
        else:
            # Convert only the block the editor explicitly requested
            self._convert_import_block(page, convert_block_id)

        if commit:
            page.save()

        return page

    # ------------------------------------------------------------------
    def _convert_all_import_blocks(self, page):
        """Convert every rich_text_import block across all StreamFields."""
        from .richtext_import_utils import html_to_stream_blocks
        base_url = self._get_base_url()
        stream_fields = ['body', 'body_en', 'body_hi', 'body_ta']

        for field_name in stream_fields:
            stream_value = getattr(page, field_name, None)
            if not stream_value:
                continue
            try:
                raw = list(stream_value.raw_data)
            except AttributeError:
                continue

            new_blocks = []
            changed = False
            for block in raw:
                if block.get('type') == 'rich_text_import':
                    changed = True
                    raw_text = block.get('value', '') or ''
                    new_blocks.extend(html_to_stream_blocks(raw_text, base_url=base_url))
                else:
                    new_blocks.append(block)

            if changed:
                setattr(page, field_name, json.dumps(new_blocks))

    # ------------------------------------------------------------------
    def _convert_import_block(self, page, block_id):
        """
        Find the rich_text_import block whose id == block_id across all
        StreamFields on the page, convert its content, and replace it
        in-place with the resulting blocks.
        """
        from .richtext_import_utils import html_to_stream_blocks

        # Construct a base URL for resolving relative image paths.
        # Uses the Wagtail Site record for the current default site.
        base_url = self._get_base_url()

        stream_fields = ['body', 'body_en', 'body_hi', 'body_ta']

        for field_name in stream_fields:
            stream_value = getattr(page, field_name, None)
            if not stream_value:
                continue

            try:
                raw = list(stream_value.raw_data)
            except AttributeError:
                continue

            new_blocks = []
            changed    = False

            for block in raw:
                if (
                    block.get('type') == 'rich_text_import'
                    and block.get('id') == block_id
                ):
                    changed  = True
                    raw_text = block.get('value', '') or ''
                    converted = html_to_stream_blocks(raw_text, base_url=base_url)
                    new_blocks.extend(converted)
                else:
                    new_blocks.append(block)

            if changed:
                # Re-assign the field as raw JSON.
                # Wagtail's StreamField descriptor accepts a JSON string
                # and parses it correctly.
                setattr(page, field_name, json.dumps(new_blocks))
                # Stop after the first field that contained the block —
                # block IDs are unique across all fields.
                break

    # ------------------------------------------------------------------
    @staticmethod
    def _get_base_url():
        """
        Return the site's base URL for resolving relative image paths,
        or None if it cannot be determined.
        """
        try:
            from wagtail.models import Site
            site = Site.objects.filter(is_default_site=True).first()
            if not site:
                return None
            scheme = 'https' if site.port == 443 else 'http'
            base = f'{scheme}://{site.hostname}'
            if site.port not in (80, 443):
                base += f':{site.port}'
            return base
        except Exception:
            return None

                

class Article(RoutablePageMixin, Page, HitCountMixin):
    base_form_class = ArticleForm

    hit_count_generic = GenericRelation(
        HitCount, object_id_field='object_pk',
        related_query_name='hit_count_generic_relation'
    )

    tags = ClusterTaggableManager(through=ArticleTag, blank=True)

    parent_page_types = ['ArticleIndexPage']
    subpage_types = []

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
        FieldPanel('slug', help_text="The slug is a URL-friendly name for this page. It is used as the end portion of the page's web address (URL), e.g., 'dr-john-watson'. It should only contain lowercase letters, numbers, and hyphens. Changing the slug will change the URL of the page and may break existing links."),
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

    promote_panels = [
        MultiFieldPanel([
            FieldPanel('seo_title'),
            FieldPanel('search_description'),
        ], heading="For Search Engines"),
        MultiFieldPanel([
            FieldPanel('show_in_menus'),
        ], heading="Settings"),
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
                    request.session.modified = True
                else:
                    show_paywall = True

        if show_paywall:
            # Provide only the first 2 body blocks for the truncated preview
            context['truncated_body'] = list(self.body)[:2] if self.body else []
            if self.body_en:
                context['truncated_body_en'] = list(self.body_en)[:2]
            if self.body_hi:
                context['truncated_body_hi'] = list(self.body_hi)[:2]
            if self.body_ta:
                context['truncated_body_ta'] = list(self.body_ta)[:2]

        context['show_paywall'] = show_paywall
        context['reader'] = reader

        # Inject approved and active comments (top-level only, replies rendered recursively)
        context['comments'] = self.comments.filter(
            is_approved=True, is_removed=False, parent__isnull=True
        ).prefetch_related('replies', 'user', 'user__profile_image')
        context['comment_count'] = self.comments.filter(is_approved=True, is_removed=False).count()

        return context

    @route(r'^pdf/$')
    def serve_pdf(self, request):
        # 1. Permission Check
        is_subscribed = request.user.is_authenticated and request.user.is_subscribed
        is_admin = request.user.is_superuser or request.user.is_staff
        
        if not (is_subscribed or is_admin):
            return redirect(self.url)

        context = self.get_context(request)
        context['lang'] = request.GET.get('lang', 'ml')
        html_string = render_to_string('articles/article_pdf.html', context)
        html = HTML(string=html_string, base_url=request.build_absolute_uri('/'))
        pdf = html.write_pdf()

        from django.utils.encoding import escape_uri_path
        lang = request.GET.get('lang', 'ml')
        filename_title = self.title
        if lang == 'en' and self.title_en:
            filename_title = self.title_en
        elif lang == 'hi' and self.title_hi:
            filename_title = self.title_hi
        elif lang == 'ta' and self.title_ta:
            filename_title = self.title_ta

        filename = f"{filename_title}.pdf"
        response = HttpResponse(pdf, content_type='application/pdf')
        # Use filename*=UTF-8'' to support non-ASCII characters (like Malayalam) in the filename
        response['Content-Disposition'] = f"attachment; filename*=UTF-8''{escape_uri_path(filename)}"
        return response

    @property
    def excerpt(self):
        for block in self.body:
            if block.block_type in ['paragraph', 'text', 'rich_text']:
                return block.value
        return ""


SORT_CHOICES = [
    ('latest',    'Latest First'),
    ('oldest',    'Oldest First'),
    ('most_read', 'Most Read (30-day views)'),
]

class ArticleIndexPage(Page):
    intro = RichTextField(blank=True)
    tags = ClusterTaggableManager(through=ArticleIndexPageTag, blank=True)
    sort_by = models.CharField(
        max_length=20,
        choices=SORT_CHOICES,
        default='latest',
        help_text="How to order issues on this page.",
    )

    max_count = 2

    content_panels = Page.content_panels + [
        FieldPanel('intro'),
        FieldPanel('sort_by'),
    ]

    subpage_types = ['Article']

    def get_context(self, request):
        context = super().get_context(request)
        from django.apps import apps
        Article = apps.get_model('articles', 'Article')
    
        if self.sort_by == 'most_read':
            from datetime import timedelta
            from django.utils import timezone
            from django.db.models import Count
            from django.contrib.contenttypes.models import ContentType
            from hitcount.models import Hit
            from wagtail.models import Page as WagtailPage
    
            one_month_ago = timezone.now() - timedelta(days=30)
            article_ct = ContentType.objects.get_for_model(Article)
            page_ct    = ContentType.objects.get_for_model(WagtailPage)
    
            live_pks = [str(pk) for pk in Article.objects.live().values_list('id', flat=True)]
    
            top_hits = (
                Hit.objects.filter(
                    created__gte=one_month_ago,
                    hitcount__content_type__in=[article_ct, page_ct],
                    hitcount__object_pk__in=live_pks,
                )
                .values('hitcount__object_pk')
                .annotate(total_views=Count('id'))
                .order_by('-total_views')
            )
            hit_map = {}
            for h in top_hits:
                try:
                    hit_map[int(h['hitcount__object_pk'])] = h['total_views']
                except (ValueError, TypeError):
                    pass
    
            articles = list(Article.objects.live())
            for article in articles:
                article.total_views = hit_map.get(article.id, 0)
            articles.sort(key=lambda x: x.total_views, reverse=True)
    
        elif self.sort_by == 'oldest':
            articles = Article.objects.live().order_by('date')
        else:
            articles = Article.objects.live().order_by('-date')
    
        # ── Pagination (10 per page) ──────────────────────────────────────────
        paginator = Paginator(articles, 10)
        page_number = request.GET.get('page')
        try:
            page_obj = paginator.page(page_number)
        except PageNotAnInteger:
            page_obj = paginator.page(1)
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages)
    
        context['articles']  = page_obj
        context['page_obj']  = page_obj
        context['paginator'] = paginator
        return context