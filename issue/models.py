from django.db import models
from django import forms
from modelcluster.fields import ParentalKey
from modelcluster.contrib.taggit import ClusterTaggableManager
from taggit.models import TaggedItemBase
from wagtail.models import Page, Orderable
from wagtail.fields import RichTextField, StreamField
from wagtail.admin.panels import FieldPanel, InlinePanel
from wagtail.admin.forms import WagtailAdminPageForm, WagtailAdminModelForm
from wagtail.snippets.models import register_snippet
from wagtail import blocks
from wagtail.search import index
from wagtail.images.blocks import ImageChooserBlock
from wagtail.embeds.blocks import EmbedBlock
from wagtail.documents.blocks import DocumentChooserBlock
from wagtail.contrib.table_block.blocks import TableBlock
from wagtail.contrib.settings.models import BaseSiteSetting, register_setting
from articles.wagtail_widgets import ColorPickerWidget

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


class Issue(Page):
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

    editorial = StreamField([
        ('heading',    blocks.RichTextBlock(form_classname="full title")),
        ('paragraph',  blocks.RichTextBlock()),
        ('image',      ImageBlock()),                                   # ← StructBlock with caption
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
    ], use_json_field=True, null=True, blank=True)

    def get_all_articles(self):
        # 1. Get articles where this is the primary issue
        primary = self.primary_articles.live()
        # 2. Get articles where this is a reprint
        reprints = [rel.article for rel in self.reprinted_articles.select_related('article').filter(article__live=True)]
        return list(primary) + reprints

    @property
    def board_members(self):
        if self.editorial_board:
            return self.editorial_board.members.all()
        return []

    content_panels = Page.content_panels + [
        FieldPanel('slug'),
        FieldPanel('tags'),
        FieldPanel('volume'),
        FieldPanel('issue_number'),
        FieldPanel('topic'),
        FieldPanel('date_of_publishing'),
        FieldPanel('cover_image'),
        FieldPanel('editorial_board'),
        InlinePanel('reprinted_articles', label="Reprinted Articles"),
        FieldPanel('editorial'),
    ]

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        reader = None
        if request.user.is_authenticated:
            try:
                reader = request.user.reader
            except Exception:
                reader = None
        context['reader'] = reader
        return context

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

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = "Topics"

class IssueIndexPage(Page):
    intro = RichTextField(blank=True)
    tags = ClusterTaggableManager(through=IssueIndexPageTag, blank=True)

    max_count = 1
    subpage_types = ['Issue']
    # Parent should be HomePage
    parent_page_types = ['home.HomePage']

    content_panels = Page.content_panels + [
        FieldPanel('intro'),
        FieldPanel('tags'),
    ]

    def get_context(self, request):
        context = super().get_context(request)
        # Get all child issues, ordered by publishing date
        context['issues'] = Issue.objects.child_of(self).live().order_by('-date_of_publishing')
        return context