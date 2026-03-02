from django.db import models
from modelcluster.fields import ParentalKey
from wagtail.models import Page, Orderable
from wagtail.fields import RichTextField
from wagtail.admin.panels import FieldPanel, InlinePanel
from wagtail.snippets.models import register_snippet

class Issue(Page):
    date_of_publishing = models.DateField("Date of publishing")
    
    # Only allow Articles to be created inside an Issue
    subpage_types = ['articles.Article']
    parent_page_types = ['IssueIndexPage']

    topic = models.ForeignKey(
        'issue.Topic', 
        null=True, 
        blank=True, 
        on_delete=models.SET_NULL, 
        related_name='issues'
    )

    def get_all_articles(self):
        # 1. Get articles where this is the primary issue
        primary = self.primary_articles.live()
        # 2. Get articles where this is a reprint
        reprints = [rel.article for rel in self.reprinted_articles.select_related('article').filter(article__live=True)]
        return list(primary) + reprints

    content_panels = Page.content_panels + [
        FieldPanel('topic'),
        FieldPanel('date_of_publishing'),
        InlinePanel('editorial_board_relationship', label="Editorial Board Members"),
        InlinePanel('reprinted_articles', label="Reprinted Articles"),
    ]

class IssueArticleReprint(Orderable):
    issue = ParentalKey('Issue', related_name='reprinted_articles', on_delete=models.CASCADE)
    article = models.ForeignKey('articles.Article', related_name='reprint_appearances', on_delete=models.CASCADE)

    panels = [
        FieldPanel('article'),
    ]

class IssueEditorialBoardRelationship(Orderable):
    page = ParentalKey('Issue', related_name='editorial_board_relationship', on_delete=models.CASCADE)
    editor = models.ForeignKey('literati.Literati', related_name='editorial_roles', on_delete=models.CASCADE)
    
    # Context-dependent roles for the editorial board
    ROLE_CHOICES = [
        ('editor', 'Editor'),
        ('associate', 'Associate Editor'),
        ('managing', 'Managing Editor'),
        ('board', 'Board Member'),
    ]
    role = models.CharField(
        max_length=50, 
        choices=ROLE_CHOICES, 
        default='editor',
        help_text="Select the role for this issue's editorial board"
    )

    panels = [
        FieldPanel('editor'),
        FieldPanel('role'),
    ]

@register_snippet
class Topic(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)

    panels = [
        FieldPanel('name'),
        FieldPanel('slug'),
    ]

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = "Topics"

class IssueIndexPage(Page):
    intro = RichTextField(blank=True)

    max_count = 1
    subpage_types = ['Issue']
    # Parent should be HomePage
    parent_page_types = ['home.HomePage']

    content_panels = Page.content_panels + [
        FieldPanel('intro')
    ]

    def get_context(self, request):
        context = super().get_context(request)
        # Get all child issues, ordered by publishing date
        context['issues'] = Issue.objects.child_of(self).live().order_by('-date_of_publishing')
        return context