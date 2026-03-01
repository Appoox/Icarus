from django.db import models

from wagtail.admin.panels import FieldPanel
from wagtail.models import Page
from wagtail.snippets.models import register_snippet


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


@register_snippet
class SiteFooter(models.Model):
    """
    A singleton-style snippet for the global site footer.
    Manage it via Wagtail Admin → Snippets → Site Footer.
    """

    site_name = models.CharField(
        max_length=100,
        default="Icarus",
        help_text="Site name displayed in the footer.",
    )
    tagline = models.CharField(
        max_length=255,
        blank=True,
        default="Exploring the frontiers of knowledge.",
        help_text="Short tagline displayed beneath the site name.",
    )
    copyright_text = models.CharField(
        max_length=255,
        blank=True,
        default="All rights reserved.",
        help_text="Copyright text (year is added automatically).",
    )

    # Navigation links (up to 4)
    nav_link_1_label = models.CharField(max_length=50, blank=True, default="Home")
    nav_link_1_url = models.CharField(max_length=255, blank=True, default="/")
    nav_link_2_label = models.CharField(max_length=50, blank=True, default="Search")
    nav_link_2_url = models.CharField(max_length=255, blank=True, default="/search/")
    nav_link_3_label = models.CharField(max_length=50, blank=True)
    nav_link_3_url = models.CharField(max_length=255, blank=True)
    nav_link_4_label = models.CharField(max_length=50, blank=True)
    nav_link_4_url = models.CharField(max_length=255, blank=True)

    panels = [
        FieldPanel("site_name"),
        FieldPanel("tagline"),
        FieldPanel("copyright_text"),
        FieldPanel("nav_link_1_label"),
        FieldPanel("nav_link_1_url"),
        FieldPanel("nav_link_2_label"),
        FieldPanel("nav_link_2_url"),
        FieldPanel("nav_link_3_label"),
        FieldPanel("nav_link_3_url"),
        FieldPanel("nav_link_4_label"),
        FieldPanel("nav_link_4_url"),
    ]

    class Meta:
        verbose_name = "Site Footer"
        verbose_name_plural = "Site Footers"

    def __str__(self):
        return self.site_name

    def nav_links(self):
        """Return a list of (label, url) tuples for non-empty nav links."""
        links = []
        for i in range(1, 5):
            label = getattr(self, f"nav_link_{i}_label", "")
            url = getattr(self, f"nav_link_{i}_url", "")
            if label and url:
                links.append({"label": label, "url": url})
        return links


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
