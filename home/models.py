from django.db import models

from wagtail.admin.panels import FieldPanel
from wagtail.models import Page
from wagtail.snippets.models import register_snippet
from modelcluster.fields import ParentalKey
from modelcluster.models import ClusterableModel # Needed for the Parent class
from wagtail.fields import RichTextField, StreamField
from wagtail.admin.panels import FieldPanel, InlinePanel, MultiFieldPanel

@register_snippet
class SiteHeader(models.Model):
    """
    A singleton-style snippet for the global site header.
    Manage it via Wagtail Admin → Snippets → Site Header.
    """

    logo = models.ForeignKey(
        "wagtailimages.Image",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Logo displayed on the left side of the header.",
    )
    logo_alt_text = models.CharField(
        max_length=100,
        blank=True,
        default="Site logo",
        help_text="Alt text for the logo image (for accessibility).",
    )
    site_title = models.CharField(
        max_length=100,
        default="My Wagtail Site",
        help_text="Title displayed in the centre of the header.",
    )
    site_title_url = models.CharField(
        max_length=255,
        blank=True,
        default="/",
        help_text="URL the site title links to (defaults to home page).",
    )

    panels = [
        FieldPanel("logo"),
        FieldPanel("logo_alt_text"),
        FieldPanel("site_title"),
        FieldPanel("site_title_url"),
    ]

    class Meta:
        verbose_name = "Site Header"
        verbose_name_plural = "Site Headers"

    def __str__(self):
        return self.site_title


class FooterLink(models.Model):
    """A single link inside the footer."""

    footer = ParentalKey(
        "SiteFooter",
        on_delete=models.CASCADE,
        related_name="links",
    )
    label = models.CharField(max_length=100)
    url = models.CharField(max_length=255)

    panels = [
        FieldPanel("label"),
        FieldPanel("url"),
    ]

    def __str__(self):
        return self.label


@register_snippet
class SiteFooter(ClusterableModel):
    """
    Singleton-style snippet for the global site footer.
    Manage it via Wagtail Admin → Snippets → Site Footer.
    """

    copyright_text = models.CharField(
        max_length=255,
        blank=True,
        default="© 2024 My Wagtail Site. All rights reserved.",
        help_text="Copyright line shown at the bottom of the footer.",
    )
    tagline = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional short tagline shown above the links.",
    )
    body = RichTextField(
        blank=True,
        help_text="Optional rich-text content (address, social links, etc.).",
    )

    panels = [
        MultiFieldPanel(
            [
                FieldPanel("tagline"),
                FieldPanel("body"),
            ],
            heading="Content",
        ),
        MultiFieldPanel(
            [
                InlinePanel("links", label="Footer Link"),
            ],
            heading="Links",
        ),
        MultiFieldPanel(
            [
                FieldPanel("copyright_text"),
            ],
            heading="Copyright",
        ),
    ]

    class Meta:
        verbose_name = "Site Footer"
        verbose_name_plural = "Site Footers"

    def __str__(self):
        return "Site Footer"

class HomePage(Page):
    def get_context(self, request):
        context = super().get_context(request)
        
        # Avoid circular imports by importing inside the method
        from articles.models import Article
        from issue.models import Issue, Topic

        # 1. Hero Article: Latest article with a cover image
        hero_article = Article.objects.live().filter(cover_image__isnull=False).order_by('-first_published_at').first()
        context['hero_article'] = hero_article

        # 2. Latest Articles (excluding hero)
        exclude_ids = [hero_article.id] if hero_article else []
        latest_articles = Article.objects.live().exclude(id__in=exclude_ids).order_by('-first_published_at')[:6]
        context['latest_articles'] = latest_articles

        # 3. Current Issue
        context['current_issue'] = Issue.objects.live().order_by('-date_of_publishing').first()

        # 4. Featured Topics (get 4 topics with their latest 3 articles)
        featured_topics = []
        topics = Topic.objects.all()[:4]
        for topic in topics:
            topic_articles = Article.objects.live().filter(topic=topic).order_by('-first_published_at')[:3]
            if topic_articles:
                featured_topics.append({
                    'name': topic.name,
                    'slug': topic.slug,
                    'articles': topic_articles
                })
        context['featured_topics'] = featured_topics

        return context
