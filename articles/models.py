from django.db import models
from modelcluster.fields import ParentalManyToManyField
from wagtail.models import Page
from wagtail.fields import RichTextField
from wagtail.admin.panels import FieldPanel

# Create your models here.

class Article(Page):
    author = ParentalManyToManyField('Author', blank = True)
    date = models.DateField(("Date of publishing"), auto_now=False, auto_now_add=False)
    body = RichTextField(blank=True)

    content_panels = Page.content_panels + [
        FieldPanel('author'),
        FieldPanel('date'),
        FieldPanel('body'),
    ]

class Author(Page):
    role = models.CharField(("Title/Role"), max_length=50)
    bio = models.TextField(("Author bio"))
    profile_image = models.ForeignKey(
        'wagtailimages.Image',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+'
    )


    panels = [
        FieldPanel('role'),
        FieldPanel('bio'),
        FieldPanel('profile_image'),
    ]

    def __str__(self):
        return self.title

    def get_articles(self):
        """Get all articles by this author"""
        return Article.objects.live().filter(author=self)