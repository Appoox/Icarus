from django.db import models
from django import forms
from django.core.exceptions import ValidationError
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
from reader.models import ReaderUser
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
    phone_number = PhoneNumberField("Phone Number", blank=False)
    address_line_1 = models.CharField(
        max_length=255, blank=True,
        help_text='House / flat number, street name.',
    )
    address_line_2 = models.CharField(
        max_length=255, blank=True,
        help_text='Landmark, area, locality.',
    )
    city = models.CharField(max_length=100, blank=True)
    post_office = models.CharField(max_length=100, blank=True)
    pincode = models.CharField(
        max_length=6, blank=True,
        validators=[ReaderUser.pincode_validator],
        help_text='6-digit Indian pincode.',
    )
    district = models.CharField(max_length=100, blank=True)
    state = models.CharField(
        max_length=50, blank=True,
        choices=ReaderUser.INDIAN_STATES,
    )
    reader_user = models.OneToOneField(
        'reader.ReaderUser',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='literati',
        verbose_name="Associated Reader User"
    )
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

    def clean(self):
        super().clean()
        
        if not self.phone_number:
            return

        from reader.models import ReaderUser

        # Check if phone number is already registered in ReaderUser
        phone_exists = ReaderUser.objects.filter(phone_number=self.phone_number)
        if self.reader_user:
            phone_exists = phone_exists.exclude(pk=self.reader_user.pk)
        if phone_exists.exists():
            raise ValidationError({'phone_number': 'A Reader User with this phone number already exists.'})

        # Check if email is already registered in ReaderUser
        if self.email:
            email_exists = ReaderUser.objects.filter(email=self.email)
            if self.reader_user:
                email_exists = email_exists.exclude(pk=self.reader_user.pk)
            if email_exists.exists():
                raise ValidationError({'email': 'A Reader User with this email already exists.'})

        # Check if another Literati page has the same phone number
        literati_phone_exists = Literati.objects.filter(phone_number=self.phone_number).exclude(pk=self.pk)
        if literati_phone_exists.exists():
            raise ValidationError({'phone_number': 'Another Author page is already registered with this phone number.'})

        # Check if another Literati page has the same email
        if self.email:
            literati_email_exists = Literati.objects.filter(email=self.email).exclude(pk=self.pk)
            if literati_email_exists.exists():
                raise ValidationError({'email': 'Another Author page is already registered with this email.'})

    def save(self, *args, **kwargs):
        if self.reader_user:
            user = self.reader_user
            updated_fields = []
            if user.phone_number != self.phone_number:
                user.phone_number = self.phone_number
                updated_fields.append('phone_number')
            if user.email != self.email:
                user.email = self.email
                updated_fields.append('email')
            if user.name != self.title:
                user.name = self.title
                updated_fields.append('name')
            if user.address_line_1 != self.address_line_1:
                user.address_line_1 = self.address_line_1
                updated_fields.append('address_line_1')
            if user.address_line_2 != self.address_line_2:
                user.address_line_2 = self.address_line_2
                updated_fields.append('address_line_2')
            if user.city != self.city:
                user.city = self.city
                updated_fields.append('city')
            if user.post_office != self.post_office:
                user.post_office = self.post_office
                updated_fields.append('post_office')
            if user.pincode != self.pincode:
                user.pincode = self.pincode
                updated_fields.append('pincode')
            if user.district != self.district:
                user.district = self.district
                updated_fields.append('district')
            if user.state != self.state:
                user.state = self.state
                updated_fields.append('state')
            if updated_fields:
                user.save(update_fields=updated_fields)
        super().save(*args, **kwargs)

    content_panels = [
        FieldPanel('title', heading="Name", help_text="Enter the full name of the person"),
        FieldPanel('slug', help_text="The slug is a URL-friendly name for this page. It is used as the end portion of the page's web address (URL). It should only contain lowercase letters, numbers, and hyphens. Changing the slug will change the URL of the page and may break existing links."),
        FieldPanel('tags'),
        FieldPanel('role'),
        FieldPanel('profile_image'),
        FieldPanel('bio'),
        MultiFieldPanel([
            FieldPanel('email'),
            FieldPanel('phone_number'),
            FieldPanel('reader_user', read_only=True),
        ], heading="Contact Information"),
        MultiFieldPanel([
            FieldPanel('address_line_1'),
            FieldPanel('address_line_2'),
            FieldPanel('city'),
            FieldPanel('post_office'),
            FieldPanel('district'),
            FieldPanel('state'),
            FieldPanel('pincode'),
        ], heading="Mailing Address"),
        FieldPanel('areas_of_interest', widget=forms.CheckboxSelectMultiple),
        FieldPanel('social_media_links'),
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

