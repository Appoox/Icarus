from django.db import models
from modelcluster.fields import ParentalKey
from wagtail.models import Page, Orderable
from wagtail.fields import RichTextField
from wagtail.admin.panels import FieldPanel

class Literati(Page):
    role = models.CharField("Title / Role of the author", blank=True)
    bio = models.TextField("Bio", blank=True)
    profile_image = models.ForeignKey(
        'wagtailimages.Image',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )

    def get_articles(self):
        return [rel.page for rel in self.literati_articles.select_related('page').filter(page__live=True)]

    content_panels = Page.content_panels + [
        FieldPanel('title'),
        FieldPanel('profile_image'),
        FieldPanel('bio'),
    ]

class AuthorIndexPage(Page):
    intro = RichTextField(blank=True)
    subpage_types = ['Literati']

    def get_context(self, request):
        context = super().get_context(request)
        context['literati'] = self.get_children().live().order_by('title')
        return context

    content_panels = Page.content_panels + [
        FieldPanel('intro'),
    ]

class ArticleAuthorRelationship(Orderable):
    # Note: Using string reference to 'articles.Article' to avoid circular imports
    page = ParentalKey('articles.Article', related_name='article_authors', on_delete=models.CASCADE)
    author = models.ForeignKey('Literati', related_name='literati_articles', on_delete=models.CASCADE)
    
    role = models.CharField(
        max_length=100, 
        default="Author", 
        help_text="e.g. Author, Photographer, Illustrator"
    )

    panels = [
        FieldPanel('author'),
        FieldPanel('role'),
    ]
