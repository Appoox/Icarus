from django.db import models
from modelcluster.fields import ParentalKey, ParentalManyToManyField
from wagtail.models import Page, Orderable
from wagtail.fields import RichTextField, StreamField
from wagtail import blocks
from wagtail.admin.panels import FieldPanel, MultiFieldPanel
from phonenumber_field.modelfields import PhoneNumberField

class SocialMediaBlock(blocks.StructBlock):
    platform = blocks.ChoiceBlock(choices=[
        ('facebook', 'Facebook'),
        ('twitter', 'Twitter'),
        ('linkedin', 'LinkedIn'),
        ('instagram', 'Instagram'),
        ('github', 'GitHub'),
        ('website', 'Website'),
    ])
    url = blocks.URLBlock()

    class Meta:
        icon = 'link'
        label = 'Social Media Link'

from django import forms
from hitcount.models import HitCountMixin, HitCount
from django.contrib.contenttypes.fields import GenericRelation
from hitcount.views import HitCountMixin as HitCountViewMixin

class Literati(Page, HitCountMixin):

    parent_page_types = ['AuthorIndexPage']

    role = models.CharField("Title / Role of the person", blank=True)
    bio = RichTextField("Bio", blank=True)
    profile_image = models.ForeignKey(
        'wagtailimages.Image',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    
    email = models.EmailField("Email", blank=True)
    phone_number = PhoneNumberField("Phone Number", blank=True)
    areas_of_interest = ParentalManyToManyField('issue.Topic', blank=True)
    social_media_links = StreamField([
        ('social_link', SocialMediaBlock()),
    ], blank=True, use_json_field=True)

    hit_count_generic = GenericRelation(
        HitCount, object_id_field='object_pk',
        related_query_name='hit_count_generic_relation'
    )
    read_fully_count = models.PositiveIntegerField(default=0, editable=False)

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        if not (request.user.is_superuser or request.user.is_staff):
            hit_count = HitCount.objects.get_for_object(self)
            HitCountViewMixin().hit_count(request, hit_count)
        return context

    def get_articles(self):
        return [rel.page for rel in self.literati_articles.select_related('page').filter(page__live=True)]

    content_panels = [
        FieldPanel('title', heading="Name", help_text="Enter the full name of the person"),
        FieldPanel('slug'),
        FieldPanel('role'),
        FieldPanel('profile_image'),
        FieldPanel('bio'),
        MultiFieldPanel([
            FieldPanel('email'),
            FieldPanel('phone_number'),
        ], heading="Contact Information"),
        FieldPanel('areas_of_interest', widget=forms.CheckboxSelectMultiple),
        FieldPanel('social_media_links'),
    ]

class AuthorIndexPage(Page):
    intro = RichTextField(blank=True)
    max_count = 1
    subpage_types = ['Literati']

    def get_context(self, request):
        context = super().get_context(request)
        context['authors'] = self.get_children().live().order_by('title')
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
