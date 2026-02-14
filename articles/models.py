from django.db import models
from modelcluster.fields import ParentalKey
from wagtail.models import Page, Orderable
from wagtail.fields import RichTextField, StreamField
from wagtail.admin.panels import FieldPanel, InlinePanel
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from wagtail import blocks
from wagtail.images.blocks import ImageChooserBlock
from wagtail.embeds.blocks import EmbedBlock
from wagtail.documents.blocks import DocumentChooserBlock
from wagtail.contrib.table_block.blocks import TableBlock
from wagtail.snippets.blocks import SnippetChooserBlock

class BlockQuoteBlock(blocks.StructBlock):
    text = blocks.TextBlock()
    attribute_name = blocks.CharBlock(
        required=False, 
        label='e.g. Mary Berry',
        help_text="The person to attribute the quote to"
    )

    class Meta:
        icon = 'openquote'
        label = 'Blockquote'
        template = 'blocks/blockquote_block.html'

# 1. THE AUTHOR PAGE
class Author(Page):
    role = models.CharField("Title/Role", max_length=50)
    bio = models.TextField("Author bio")
    profile_image = models.ForeignKey(
        'wagtailimages.Image',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )


    def get_articles(self):
        # We look at ArticleAuthorRelationship (related_name='author_articles')
        # and pull the 'page' (the Article) from each relationship.
        return [rel.page for rel in self.author_articles.select_related('page').filter(page__live=True)]

    
    content_panels = Page.content_panels + [
        FieldPanel('role'),
        FieldPanel('bio'),
        FieldPanel('profile_image'),
    ]

class ArticleAuthorRelationship(Orderable):
    page = ParentalKey('Article', related_name='article_authors', on_delete=models.CASCADE)
    author = models.ForeignKey('Author', related_name='author_articles', on_delete=models.CASCADE)

    panels = [
        FieldPanel('author'),
    ]

# 3. THE ARTICLE PAGE
class Article(Page):
    date = models.DateField("Date of publishing")
    body = StreamField([
        ('heading', blocks.CharBlock(form_classname="full title")),
        ('paragraph', blocks.RichTextBlock()),
        ('image', ImageChooserBlock()),
        ('blockquote', BlockQuoteBlock()),
        ('embed', EmbedBlock(help_text="Insert a URL to embed (e.g. YouTube, Vimeo, SoundCloud)")),
        ('document', DocumentChooserBlock()),
        ('table', TableBlock()),
        ('raw_html', blocks.RawHTMLBlock(help_text="Use with caution: raw HTML is not sanitised")),
        ('text', blocks.TextBlock()),
        ('email', blocks.EmailBlock()),
        ('url', blocks.URLBlock()),
        ('boolean', blocks.BooleanBlock(required=False)),
        ('integer', blocks.IntegerBlock()),
        ('float', blocks.FloatBlock()),
        ('decimal', blocks.DecimalBlock()),
        ('date', blocks.DateBlock()),
        ('time', blocks.TimeBlock()),
        ('datetime', blocks.DateTimeBlock()),
        ('rich_text', blocks.RichTextBlock(label="Rich Text (Full)")),
        ('choice', blocks.ChoiceBlock(choices=[
            ('left', 'Left'),
            ('center', 'Centre'),
            ('right', 'Right'),
        ], help_text="Alignment choice")),
        ('page', blocks.PageChooserBlock()),
        ('static', blocks.StaticBlock(
            admin_text="This is a placeholder block — content is defined in the template.",
            label="Divider / Separator",
        )),
        ('list', blocks.ListBlock(blocks.CharBlock(), label="List of items")),
        ('stream', blocks.StreamBlock([
            ('text', blocks.CharBlock()),
            ('image', ImageChooserBlock()),
        ], label="Nested Stream")),
    ], use_json_field=True, null=True, blank=True)

    cover_image = models.ForeignKey(
        'wagtailimages.Image',
        null = True,
        blank = True,
        on_delete = models.SET_NULL,
        related_name='+',
    )

    content_panels = Page.content_panels + [
        FieldPanel('date'),
        FieldPanel('cover_image'),
        InlinePanel('article_authors', label="Authors"),
        FieldPanel('body'),
    ]

    @property
    def excerpt(self):
        for block in self.body:
            if block.block_type in ['paragraph', 'text', 'rich_text']:
                return block.value
        return ""

class ArticleIndexPage(Page):
    intro = RichTextField(blank=True)

    content_panels = Page.content_panels + [
        FieldPanel('intro')
    ]

    # This ensures only Articles can be added under this page
    subpage_types = ['Article']

    def get_context(self, request):
        context = super().get_context(request)
        
        # Get all published articles, ordered by newest first
        all_articles = self.get_children().live().order_by('-first_published_at')

        context['articles'] = all_articles
        return context

class AuthorIndexPage(Page):
    intro = RichTextField(blank=True)

    # This ensures only Author pages can be created under this index
    subpage_types = ['Author']

    def get_context(self, request):
        context = super().get_context(request)
        # Fetch all child pages that are live
        authors = self.get_children().live().order_by('title')
        context['authors'] = authors
        return context

    content_panels = Page.content_panels + [
        FieldPanel('intro'),
    ]