from django.db import models
from django import forms
from modelcluster.fields import ParentalKey
from modelcluster.contrib.taggit import ClusterTaggableManager
from taggit.models import TaggedItemBase
from wagtail.models import Page, Orderable
from wagtail.fields import RichTextField, StreamField
from wagtail.admin.panels import FieldPanel, InlinePanel, MultiFieldPanel, PageChooserPanel
from wagtail.admin.forms import WagtailAdminPageForm, WagtailAdminModelForm
from wagtail.snippets.models import register_snippet
from wagtail import blocks
from wagtail.search import index
from wagtail.images.blocks import ImageChooserBlock
from wagtail.embeds.blocks import EmbedBlock
from wagtail.documents.blocks import DocumentChooserBlock
from wagtail.contrib.table_block.blocks import TableBlock
from wagtail.contrib.settings.models import BaseSiteSetting, register_setting
from articles.wagtail_widgets import ColorPickerWidget, OptionalColourWidget
from home.theme import ThemedTemplateMixin, hex_colour_validator
from articles.models import AudioBlock, VideoBlock
from django.contrib.contenttypes.fields import GenericRelation
from hitcount.models import HitCount
from hitcount.utils import get_hitcount_model
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.utils.functional import cached_property
from articles.wagtail_widgets import ColorPickerBlock
from articles.models import ColoredHeadingBlock, BlockQuoteBlock, ImageBlock, AudioBlock, VideoBlock, RichTextImportBlock


class IssueTag(TaggedItemBase):
    content_object = ParentalKey(
        'Issue',
        related_name='tagged_items',
        on_delete=models.CASCADE
    )

class IssueIndexPageTag(TaggedItemBase):
    content_object = ParentalKey(
        'IssueIndexPage',
        related_name='tagged_items',
        on_delete=models.CASCADE
    )

class VolumeForm(WagtailAdminModelForm):
    """Pre-fills the number field with the next volume number (last + 1)."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only pre-fill when creating a new volume (no pk yet)
        if not self.instance.pk:
            from issue.models import Volume as VolModel
            latest = VolModel.objects.order_by('-number').first()
            next_number = (latest.number + 1) if latest else 1
            self.fields['number'].initial = next_number
            self.initial['number'] = next_number
            # Force the value into the widget so it renders in the HTML input
            self.fields['number'].widget.attrs['value'] = next_number


@register_snippet
class Volume(index.Indexed, models.Model):
    number = models.PositiveIntegerField(unique=True, help_text="Volume number (e.g. 1, 2, 3…)")
    year_start = models.PositiveIntegerField(help_text="Starting year of the volume (e.g. 2024)")
    year_end = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Ending year if the volume spans multiple years. Leave blank for a single-year volume."
    )

    search_fields = [
        index.SearchField('number'),
    ]

    panels = [
        FieldPanel('number'),
        FieldPanel('year_start'),
        FieldPanel('year_end'),
    ]

    base_form_class = VolumeForm

    def __str__(self):
        if self.year_end and self.year_end != self.year_start:
            return f"Volume {self.number} ({self.year_start}–{self.year_end})"
        return f"Volume {self.number} ({self.year_start})"

    class Meta:
        ordering = ['-number']
        verbose_name_plural = "Volumes"


class IssueForm(WagtailAdminPageForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            latest_volume = Volume.objects.order_by('-number').first()
            if latest_volume:
                self.fields['volume'].queryset = Volume.objects.filter(pk=latest_volume.pk)
                self.fields['volume'].initial = latest_volume.pk
                self.initial['volume'] = latest_volume.pk
                self.fields['volume'].widget = forms.Select(choices=self.fields['volume'].choices)
                self.fields['volume'].required = False

            # Pre-fill title with the next month's Malayalam name
            from datetime import date
            MALAYALAM_MONTHS = {
                1: 'ജനുവരി',
                2: 'ഫെബ്രുവരി',
                3: 'മാര്ച്ച്',
                4: 'ഏപ്രില്',
                5: 'മെയ്',
                6: 'ജൂണ്',
                7: 'ജൂലൈ',
                8: 'ആഗസ്റ്റ്',
                9: 'സെപ്തംബര്',
                10: 'ഒക്ടോബര്',
                11: 'നവംബര്',
                12: 'ഡിസംബര്',
            }
            today = date.today()
            next_month = today.month % 12 + 1
            next_year = today.year + 1 if today.month == 12 else today.year
            next_title = f"{MALAYALAM_MONTHS[next_month]} {next_year}"
            if 'title' in self.fields:
                self.initial['title'] = next_title
                self.fields['title'].initial = next_title
                self.fields['title'].widget.attrs['value'] = next_title

            from issue.models import Issue as IssueModel
            latest_issue = IssueModel.objects.order_by('-pk').first()
            next_issue_num = 1
            if latest_issue and latest_issue.issue_number is not None:
                if latest_volume and latest_issue.volume != latest_volume:
                    next_issue_num = 1
                else:
                    next_issue_num = latest_issue.issue_number + 1

            if 'issue_number' in self.fields:
                self.initial['issue_number'] = next_issue_num
                self.fields['issue_number'].initial = next_issue_num
                self.fields['issue_number'].widget.attrs['value'] = next_issue_num


from wagtail.contrib.routable_page.models import RoutablePageMixin, route
from django.http import HttpResponse, Http404
from django.shortcuts import redirect

CONTENTS_ORDER_CHOICES = [
    ('by_topic', 'By topic — the cover theme first, then each topic'),
    ('by_page', 'By page — one list in print order'),
]


class Issue(ThemedTemplateMixin, RoutablePageMixin, Page):
    base_form_class = IssueForm
    volume = models.ForeignKey(
        'Volume',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='issues',
        help_text="The volume this issue belongs to"
    )
    issue_number = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Issue number within the volume (e.g. 1, 2, 3…)"
    )
    date_of_publishing = models.DateField("Date of publishing")

    # Set once the new-issue newsletter has gone out (reader.tasks), so later
    # re-publishes of the same issue — typo fixes, new PDFs — never mail
    # every subscriber again.
    newsletter_sent_at = models.DateTimeField(null=True, blank=True, editable=False)
    tags = ClusterTaggableManager(through=IssueTag, blank=True)

    # Only allow Articles to be created inside an Issue
    subpage_types = ['articles.Article']
    parent_page_types = ['IssueIndexPage']

    cover_image = models.ForeignKey(
        'wagtailimages.Image',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )

    featured_article = models.ForeignKey(
        'articles.Article',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
        help_text=(
            "The lead article for this issue. The homepage's Featured Article "
            "section can be set to follow this, so setting it here is enough — "
            "the homepage updates itself when the next issue publishes."
        ),
    )

    topic = models.ForeignKey(
        'issue.Topic', 
        null=True, 
        blank=True, 
        on_delete=models.SET_NULL, 
        related_name='issues'
    )

    editorial_board = models.ForeignKey(
        'literati.EditorialBoard',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='issues',
        help_text="Select an editorial board group for this issue"
    )

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

    # ── PDF ──────────────────────────────────────────────────────────────
    PDF_file = models.ForeignKey(
        'wagtaildocs.Document',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
        help_text="ലക്കം അപ്‌ലോഡ് ചെയ്യാം"
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

    hit_count_generic = GenericRelation(
        HitCount,
        object_id_field='object_pk',
        content_type_field='content_type',
        related_query_name='issue_hitcount'
    )

    # ── Print edition design ──────────────────────────────────────────────
    # Used only when Settings → Site theme is "Print edition".  All optional:
    # whatever is left empty is simply not shown.
    colour = models.CharField(
        "Issue colour",
        max_length=7,
        blank=True,
        validators=[hex_colour_validator],
        help_text=(
            "One colour for this issue, used on the issue, its articles and "
            "(while it is the current issue) the homepage. Stripes, tags, "
            "buttons, panel borders and covers use it exactly as picked; "
            "headlines and links use it too, made only as much darker as a "
            "light colour needs to stay readable on the page. Type it any way "
            "(#fc0, rgb(…), gold) or pick it with the swatch. Empty = the "
            "default colour in Settings → Site theme."
        ),
    )
    standfirst = models.TextField(
        "Summary",
        blank=True,
        help_text="Two or three sentences introducing the issue's theme, shown under it.",
    )
    page_count = models.PositiveSmallIntegerField(
        "Pages", null=True, blank=True,
        help_text="Number of pages in the print edition, e.g. 60.",
    )
    price = models.PositiveSmallIntegerField(
        "Price (₹)", null=True, blank=True,
        help_text="Cover price in rupees, e.g. 30.",
    )
    editorial_heading = models.CharField(
        max_length=120, blank=True,
        help_text=(
            "Heading over the editorial, e.g. ഗസ്റ്റ് എഡിറ്ററുടെ കുറിപ്പ്. "
            "Empty = മുഖക്കുറിപ്പ്."
        ),
    )
    editorial_signature = models.CharField(
        max_length=200, blank=True,
        help_text="Who signed the editorial, e.g. ഡോ. സെബാസ്റ്റ്യൻ കൂത്തോട്ടിൽ, ഗസ്റ്റ് എഡിറ്റർ.",
    )
    contents_order = models.CharField(
        max_length=10,
        choices=CONTENTS_ORDER_CHOICES,
        default='by_topic',
        help_text="How the contents are listed on the issue page.",
    )
    next_issue_theme = models.CharField(
        "Next issue's theme", max_length=120, blank=True,
        help_text="Announced at the bottom of this issue's page, e.g. ഭൂഗർഭജലം.",
    )
    next_issue_colour = models.CharField(
        "Next issue's colour",
        max_length=7,
        blank=True,
        validators=[hex_colour_validator],
        help_text="Colour of that announcement. Empty = this issue's colour.",
    )

    editorial = StreamField([
        ('rich_text_import', RichTextImportBlock()),

    # ── Media ──────────────────────────────────────────────────────────────
    ('audio',           AudioBlock()),
    ('video',           VideoBlock()),

    # ── Text ───────────────────────────────────────────────────────────────
    ('heading',         blocks.RichTextBlock(form_classname="full title")),
    ('colored_heading', ColoredHeadingBlock(label="Colored Heading")),
    ('paragraph',       blocks.RichTextBlock()),
    ('blockquote',      BlockQuoteBlock()),
    ('text',            blocks.TextBlock()),

    # ── Media & Embeds ─────────────────────────────────────────────────────
    ('image',           ImageBlock()),
    ('embed',           EmbedBlock(help_text="Insert a URL to embed (e.g. YouTube, Vimeo, SoundCloud)")),
    ('document',        DocumentChooserBlock()),
    ('table',           TableBlock()),
    ('raw_html',        blocks.RawHTMLBlock(help_text="Use with caution: raw HTML is not sanitised")),

    # ── Typed primitives ───────────────────────────────────────────────────
    ('email',           blocks.EmailBlock()),
    ('url',             blocks.URLBlock()),
    ('boolean',         blocks.BooleanBlock(required=False)),
    ('integer',         blocks.IntegerBlock()),
    ('float',           blocks.FloatBlock()),
    ('decimal',         blocks.DecimalBlock()),
    ('date',            blocks.DateBlock()),
    ('time',            blocks.TimeBlock()),
    ('datetime',        blocks.DateTimeBlock()),
    ('rich_text',       blocks.RichTextBlock(label="Rich Text (Full)")),
    ('choice',          blocks.ChoiceBlock(choices=[
        ('left',   'Left'),
        ('center', 'Centre'),
        ('right',  'Right'),
    ], help_text="Alignment choice")),
    ('page',            blocks.PageChooserBlock()),
    ('static',          blocks.StaticBlock(
        admin_text="This is a placeholder block — content is defined in the template.",
        label="Divider / Separator",
    )),
    ('list',            blocks.ListBlock(blocks.CharBlock(), label="List of items")),
    ('stream',          blocks.StreamBlock([
        ('text',  blocks.CharBlock()),
        ('image', ImageChooserBlock()),
    ], label="Nested Stream")),
    ], use_json_field=True, null=True, blank=True)

    def get_all_articles(self):
        # 1. Get articles where this is the primary issue
        primary = self.primary_articles.live()
        # 2. Get articles where this is a reprint
        reprints = [rel.article for rel in self.reprinted_articles.select_related('article').filter(article__live=True)]
        
        all_articles = list(primary) + reprints
        
        # Sort by topic name so that Django's regroup tag clusters identical topics together.
        # Articles without a topic are sorted to the end.
        all_articles.sort(key=lambda a: a.topic.name if getattr(a, 'topic', None) else "\uffff")
        
        return all_articles

    # ── Print edition helpers ─────────────────────────────────────────────

    @cached_property
    def print_articles(self):
        """
        Every article in this issue, in print order: by first page, then
        title.  Articles without a page number go last.  Authors are
        prefetched, since every list in the print theme shows them.
        """
        from articles.models import Article

        primary = list(
            self.primary_articles.live()
            .select_related('topic', 'cover_image')
            .prefetch_related('article_authors__author')
        )
        reprint_ids = list(
            self.reprinted_articles.filter(article__live=True)
            .values_list('article_id', flat=True)
        )
        reprints = list(
            Article.objects.filter(pk__in=reprint_ids)
            .select_related('topic', 'cover_image')
            .prefetch_related('article_authors__author')
        )
        articles = primary + [a for a in reprints if a.pk not in {p.pk for p in primary}]
        articles.sort(key=lambda a: (
            a.print_page_start is None,
            a.print_page_start or 0,
            a.title,
        ))
        return articles

    @property
    def feature_articles(self):
        """The main articles — everything but the regular columns (which carry a column name)."""
        return [a for a in self.print_articles if not a.kicker]

    @property
    def cover_story_articles(self):
        """The articles on this issue's theme (its topic)."""
        if not self.topic_id:
            return []
        return [a for a in self.print_articles if a.topic_id == self.topic_id]

    @property
    def cover_story_authors(self):
        names = []
        for article in self.cover_story_articles:
            for rel in article.article_authors.all():
                if rel.author.title not in names:
                    names.append(rel.author.title)
        return names

    @property
    def contents_groups(self):
        """
        The contents as the issue page lists them: one group per topic in
        "by topic" order (this issue's theme first, the rest in the order
        they first appear in print, untagged articles last), or a single
        unheaded group in "by page" order.
        """
        articles = self.print_articles
        if self.contents_order == 'by_page' or not articles:
            return [{'heading': '', 'is_theme': False, 'items': articles}] if articles else []

        groups = {}
        order = []
        for article in articles:
            key = article.topic_id or 0
            if key not in groups:
                groups[key] = {
                    'heading': article.topic.name if article.topic_id else '',
                    'is_theme': bool(self.topic_id) and key == self.topic_id,
                    'items': [],
                }
                order.append(key)
            groups[key]['items'].append(article)

        order.sort(key=lambda key: (not groups[key]['is_theme'], key == 0))
        return [groups[key] for key in order]

    @property
    def volume_label(self):
        """'വാല്യം 60 · ലക്കം 1' (in the reader's language)."""
        from django.utils.translation import gettext as _

        parts = []
        if self.volume_id:
            parts.append(f"{_('വാല്യം')} {self.volume.number}")
        if self.issue_number:
            parts.append(f"{_('ലക്കം')} {self.issue_number}")
        return ' · '.join(parts)

    @cached_property
    def archive_issue(self):
        """The scanned PDF in the archive for this issue's month, if any (for the flipbook)."""
        from the_librarian.models import ArchiveIssue

        if not self.date_of_publishing:
            return None
        return ArchiveIssue.objects.filter(
            year=self.date_of_publishing.year,
            month=self.date_of_publishing.month,
        ).first()

    @cached_property
    def board_members(self):
        """
        Members of this issue's board who were serving on the date this
        issue was published — historical issues keep showing the board as
        it stood at publication.

        Source of truth is EditorialBoardMembershipHistory: one immutable
        row per stint, so a member who left and later rejoined the same
        board still appears on issues published during ANY of their stints
        (the live member row only carries the latest stint's dates), and a
        member whose row was deleted outright still appears on issues from
        their closed stints. Live member rows are consulted only for
        editors with no history at all (rows created before the history
        model existed).

        Boundary: leaving on the publication date still counts as serving.
        Cached per instance — the four role properties below all call this.
        """
        if not self.editorial_board:
            return []
        pub = self.date_of_publishing

        def serving(joined, left):
            if pub and joined and joined > pub:
                return False  # joined after this issue was published
            if pub and left and left < pub:
                return False  # had already left when this issue published
            return True

        members = []
        added = set()        # editor ids already in the result
        has_history = set()  # editor ids covered by history (authoritative)

        stints = (
            self.editorial_board.membership_history
            .select_related('editor')
            .order_by('joined_at')
        )
        for stint in stints:
            has_history.add(stint.editor_id)
            if not serving(stint.joined_at, stint.left_at):
                continue
            if stint.editor_id in added:
                continue  # defensive: overlapping stints in dirty data
            added.add(stint.editor_id)
            members.append(stint)

        # ── Legacy fallback: member rows predating the history model ──
        for m in self.editorial_board.members.select_related('editor'):
            if m.editor_id in has_history:
                continue  # history is authoritative for this editor
            if not serving(m.joined_at, m.left_at):
                continue
            if m.joined_at is None and m.left_at is None and not m.is_active:
                continue  # undated legacy row: honour the flag
            members.append(m)

        return members

    @property
    def editors_list(self):
        return [m for m in self.board_members if m.role == 'editor']

    @property
    def associate_editors_list(self):
        return [m for m in self.board_members if m.role == 'associate']

    @property
    def managing_editors_list(self):
        return [m for m in self.board_members if m.role == 'managing']

    @property
    def board_members_only(self):
        return [m for m in self.board_members if m.role == 'board']

    
    content_panels = Page.content_panels + [
        FieldPanel('slug', help_text="The slug is a URL-friendly name for this page. It is used as the end portion of the page's web address (URL). It should only contain lowercase letters, numbers, and hyphens. Changing the slug will change the URL of the page and may break existing links."),
        FieldPanel('tags'),
        FieldPanel('volume'),
        FieldPanel('issue_number'),
        FieldPanel('topic'),
        FieldPanel('date_of_publishing'),
        FieldPanel('cover_image'),
        PageChooserPanel('featured_article', 'articles.Article'),
        FieldPanel('PDF_file'),
        MultiFieldPanel([
            FieldPanel('audio_file'),
            FieldPanel('audio_embed_url'),
        ], heading="Audio"),
        MultiFieldPanel([
            FieldPanel('video_file'),
            FieldPanel('video_embed_url'),
        ], heading="Video"),
        FieldPanel('editorial_board'),
        InlinePanel('reprinted_articles', label="Reprinted Articles"),
        FieldPanel('editorial'),
        MultiFieldPanel([
            FieldPanel('colour', widget=OptionalColourWidget),
            FieldPanel('standfirst'),
            FieldPanel('page_count'),
            FieldPanel('price'),
            FieldPanel('editorial_heading'),
            FieldPanel('editorial_signature'),
            FieldPanel('contents_order'),
            FieldPanel('next_issue_theme'),
            FieldPanel('next_issue_colour', widget=OptionalColourWidget),
        ], heading="Print edition design",
           help_text="Used only by the Print edition site design. All optional.",
           classname="collapsible"),
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

    def serve(self, request, *args, **kwargs):
        """
        Overrides the native Wagtail page serving process to capture 
        and record user view tracking metrics via django-hitcount.
        """
        from hitcount.views import HitCountMixin as HitCountViewProcessor
        from home.hit_filters import is_countable_human

        # Safely attempt to record an analytics hit without disrupting page delivery if it fails.
        # is_countable_human skips staff, bot user-agents, and datacenter IPs.
        try:
            if is_countable_human(request):
                hit_count_obj = get_hitcount_model().objects.get_for_object(self)
                HitCountViewProcessor.hit_count(request, hit_count_obj)
        except Exception:
            # Silence background analytics exceptions to ensure the end-user always receives the page
            pass

        return super().serve(request, *args, **kwargs)

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        reader = None
        if request.user.is_authenticated:
            try:
                reader = request.user
            except Exception:
                reader = None
        context['reader'] = reader

        # Inject approved comments (top-level only, replies rendered recursively).
        # Removed comments are kept when they still have renderable replies so the
        # thread survives as a "deleted" placeholder; should_render filters the rest.
        top_level_comments = self.comments.filter(
            is_approved=True, parent__isnull=True
        ).prefetch_related('replies', 'user', 'user__profile_image')
        context['comments'] = [c for c in top_level_comments if c.should_render]
        context['comment_count'] = self.comments.filter(is_approved=True, is_removed=False).count()

        return context

    @route(r'^pdf/$')
    def serve_pdf(self, request):
        # 1. Permission Check — the same rule as every other archive path
        from Icarus.access import may_read_archive
        if not may_read_archive(request.user):
            return redirect(self.url)

        # 2. File Check
        if not self.PDF_file:
            raise Http404("No PDF file uploaded for this issue.")

        # 3. Serve the file
        try:
            from django.utils.encoding import escape_uri_path
            file_data = self.PDF_file.file.read()
            filename = f"{self.title}.pdf"
            response = HttpResponse(file_data, content_type='application/pdf')
            # Use filename*=UTF-8'' to support non-ASCII characters (like Malayalam) in the filename
            response['Content-Disposition'] = f"attachment; filename*=UTF-8''{escape_uri_path(filename)}"
            return response
        except Exception as e:
            raise Http404(f"Error reading PDF file: {str(e)}")
    @property
    def is_current_issue(self):
        """Checks if this issue instance is the most recently published live issue."""
        latest = Issue.objects.live().order_by('-date_of_publishing').first()
        return latest and self.id == latest.id

class IssueArticleReprint(Orderable):
    issue = ParentalKey('Issue', related_name='reprinted_articles', on_delete=models.CASCADE)
    article = models.ForeignKey('articles.Article', related_name='reprint_appearances', on_delete=models.CASCADE)

    panels = [
        FieldPanel('article'),
    ]



@register_snippet
class Topic(index.Indexed, models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    color = models.CharField(
        max_length=7,
        default="#000000",
        help_text="Choose a color for this topic (hex format, e.g., #FF0000)"
    )

    search_fields = [
        index.SearchField('name'),
        index.SearchField('slug'),
    ]

    panels = [
        FieldPanel('name'),
        FieldPanel('slug'),
        FieldPanel('color', widget=ColorPickerWidget),
    ]
    def title(self):
        """
        Fallback property providing interface alignment across generic dashboard components.
        Maps the .title lookup directly to the underlying model's .name attribute to
        prevent Django template system VariableDoesNotExist exceptions.
        """
        return self.name

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['-id']
        verbose_name_plural = "Topics"

SORT_CHOICES = [
    ('latest',    'Latest First'),
    ('oldest',    'Oldest First'),
    ('most_read', 'Most Read (30-day views)'),
]

class IssueIndexPage(ThemedTemplateMixin, Page):
    intro = RichTextField(blank=True)
    sort_by = models.CharField(
        max_length=20,
        choices=SORT_CHOICES,
        default='latest',
        help_text="How to order issues on this page.",
    )

    content_panels = Page.content_panels + [
        FieldPanel('intro'),
        FieldPanel('sort_by'),
    ]

    subpage_types = ['Issue']
 
    def get_context(self, request):
        context = super().get_context(request)
        from django.apps import apps
        Issue = apps.get_model('issue', 'Issue')
    
        if self.sort_by == 'most_read':
            from datetime import timedelta
            from django.utils import timezone
            from django.db.models import Count
            from django.contrib.contenttypes.models import ContentType
            from hitcount.models import Hit
            from wagtail.models import Page as WagtailPage
    
            one_month_ago = timezone.now() - timedelta(days=30)
            issue_ct = ContentType.objects.get_for_model(Issue)
            page_ct  = ContentType.objects.get_for_model(WagtailPage)
    
            live_pks = [str(pk) for pk in Issue.objects.live().values_list('id', flat=True)]
    
            top_hits = (
                Hit.objects.filter(
                    created__gte=one_month_ago,
                    hitcount__content_type__in=[issue_ct, page_ct],
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
    
            issues = list(Issue.objects.live())
            for issue in issues:
                issue.total_views = hit_map.get(issue.id, 0)
            issues.sort(key=lambda x: x.total_views, reverse=True)
    
        elif self.sort_by == 'oldest':
            issues = Issue.objects.live().order_by('date_of_publishing')
        else:
            issues = Issue.objects.live().order_by('-date_of_publishing')
    
        # ── Pagination (5 per page) ───────────────────────────────────────────
        paginator = Paginator(issues, 5)
        page_number = request.GET.get('page')
        try:
            page_obj = paginator.page(page_number)
        except PageNotAnInteger:
            page_obj = paginator.page(1)
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages)
    
        context['issues']    = page_obj          # replaces the plain queryset
        context['page_obj']  = page_obj
        context['paginator'] = paginator
        return context