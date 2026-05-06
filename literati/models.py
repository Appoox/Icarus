from django.db import models
from django import forms
from modelcluster.fields import ParentalKey, ParentalManyToManyField
from modelcluster.contrib.taggit import ClusterTaggableManager
from taggit.models import TaggedItemBase
from wagtail.models import Page, Orderable
from wagtail.fields import RichTextField, StreamField
from wagtail import blocks
from wagtail.admin.panels import FieldPanel, MultiFieldPanel, InlinePanel
from phonenumber_field.modelfields import PhoneNumberField
from hitcount.models import HitCountMixin, HitCount
from django.contrib.contenttypes.fields import GenericRelation
from hitcount.views import HitCountMixin as HitCountViewMixin
from wagtail.snippets.models import register_snippet
from wagtail.search import index
from modelcluster.models import ClusterableModel


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

class LiteratiTag(TaggedItemBase):
    content_object = ParentalKey(
        'Literati',
        related_name='tagged_items',
        on_delete=models.CASCADE
    )

class AuthorIndexPageTag(TaggedItemBase):
    content_object = ParentalKey(
        'AuthorIndexPage',
        related_name='tagged_items',
        on_delete=models.CASCADE
    )

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

    tags = ClusterTaggableManager(through=LiteratiTag, blank=True)

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
        FieldPanel('tags'),
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
    tags = ClusterTaggableManager(through=AuthorIndexPageTag, blank=True)
    max_count = 1
    subpage_types = ['Literati']

    def get_context(self, request):
        context = super().get_context(request)
        context['authors'] = self.get_children().live().order_by('title')
        return context

    content_panels = Page.content_panels + [
        FieldPanel('intro'),
        FieldPanel('tags'),
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

@register_snippet
class EditorialBoard(index.Indexed, ClusterableModel):
    name = models.CharField(max_length=255)
    
    search_fields = [
        index.SearchField('name'),
    ]

    panels = [
        FieldPanel('name'),
        InlinePanel('members', label="Board Members"),
    ]

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = "Editorial Boards"


class EditorialBoardMember(Orderable):
    board = ParentalKey(EditorialBoard, related_name='members', on_delete=models.CASCADE)
    editor = models.ForeignKey('Literati', related_name='board_memberships', on_delete=models.CASCADE)
    
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
    )

    panels = [
        FieldPanel('editor'),
        FieldPanel('role'),
    ]

