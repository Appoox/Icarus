from django.db import models
from modelcluster.fields import ParentalKey
from wagtail.models import Page, Orderable
from wagtail.fields import RichTextField
from wagtail.admin.panels import FieldPanel, InlinePanel

# 1. THE AUTHOR PAGE
class Author(Page):
    role = models.CharField("Title/Role", max_length=50)
    bio = models.TextField("Author bio")
    profile_image = models.ForeignKey(
        'wagtailimages.Image',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+'
    )

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
    body = RichTextField(blank=True)

    content_panels = Page.content_panels + [
        FieldPanel('date'),
        # We use InlinePanel to manage the relationship
        InlinePanel('article_authors', label="Authors"),
        FieldPanel('body'),
    ]