from django.db import models

from wagtail.admin.panels import FieldPanel
from wagtail.models import Page
from wagtail.snippets.models import register_snippet
from modelcluster.fields import ParentalKey
from modelcluster.models import ClusterableModel
from wagtail.fields import RichTextField, StreamField
from wagtail.admin.panels import FieldPanel, InlinePanel, MultiFieldPanel

from auditlog.models import AbstractLogEntry

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
    organization_title = models.CharField(
        max_length=100,
        blank=True,
        help_text="Organization name displayed to the right of the logo.",
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
    site_title_image = models.ForeignKey(
        "wagtailimages.Image",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Image displayed in the centre of the header (replaces site_title text if set).",
    )

    panels = [
        FieldPanel("logo"),
        FieldPanel("logo_alt_text"),
        FieldPanel("organization_title"),
        FieldPanel("site_title"),
        FieldPanel("site_title_image"),
        FieldPanel("site_title_url"),
    ]

    class Meta:
        verbose_name = "Site Header"
        verbose_name_plural = "Site Headers"

    def __str__(self):
        return self.site_title


FOOTER_COLUMN_CHOICES = [
    ("col1", "Column 1"),
    ("col2", "Column 2"),
    ("col3", "Column 3"),
]


class FooterLink(models.Model):
    """A single link inside the footer, assigned to one of three columns."""

    footer = ParentalKey(
        "SiteFooter",
        on_delete=models.CASCADE,
        related_name="links",
    )
    label = models.CharField(max_length=100)
    url = models.CharField(max_length=255)
    column = models.CharField(
        max_length=10,
        choices=FOOTER_COLUMN_CHOICES,
        default="col1",
        help_text="Which column this link appears in.",
    )
    sort_order = models.IntegerField(
        default=0,
        help_text="Lower numbers appear first within the column.",
    )

    panels = [
        FieldPanel("label"),
        FieldPanel("url"),
        FieldPanel("column"),
        FieldPanel("sort_order"),
    ]

    class Meta:
        ordering = ["column", "sort_order", "label"]

    def __str__(self):
        return f"{self.label} ({self.get_column_display()})"


@register_snippet
class SiteFooter(ClusterableModel):
    """
    Singleton-style snippet for the global site footer.
    Manage it via Wagtail Admin → Snippets → Site Footer.

    Supports three link columns (each with a heading), an about paragraph,
    social-media URLs, a footer logo, and copyright text.
    """

    # ── Column headings ────────────────────────────────────────────────────
    col1_header = models.CharField(
        max_length=100,
        blank=True,
        default="Explore",
        help_text="Heading for the first link column.",
    )
    col2_header = models.CharField(
        max_length=100,
        blank=True,
        default="Company",
        help_text="Heading for the second link column.",
    )
    col3_header = models.CharField(
        max_length=100,
        blank=True,
        default="More",
        help_text="Heading for the third link column.",
    )

    # ── About / tagline text ───────────────────────────────────────────────
    about_text = models.TextField(
        blank=True,
        help_text="Descriptive paragraph shown below the link columns (e.g. ownership/editorial independence statement).",
    )

    # ── Branding ───────────────────────────────────────────────────────────
    footer_logo = models.ForeignKey(
        "wagtailimages.Image",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Logo shown in the bottom-left of the footer.",
    )
    logo_alt_text = models.CharField(
        max_length=100,
        blank=True,
        default="Site logo",
    )

    # ── Copyright ──────────────────────────────────────────────────────────
    copyright_text = models.CharField(
        max_length=255,
        blank=True,
        default="© 2024 My Wagtail Site. All rights reserved.",
        help_text="Copyright line shown at the bottom of the footer.",
    )

    # ── Social-media URLs ──────────────────────────────────────────────────
    facebook_url = models.URLField(blank=True, help_text="Full URL including https://")
    instagram_url = models.URLField(blank=True)
    linkedin_url = models.URLField(blank=True)
    youtube_url = models.URLField(blank=True)
    tiktok_url = models.URLField(blank=True)
    reddit_url = models.URLField(blank=True)
    twitter_url = models.URLField(blank=True, help_text="X / Twitter URL")

    panels = [
        MultiFieldPanel(
            [
                FieldPanel("col1_header"),
                FieldPanel("col2_header"),
                FieldPanel("col3_header"),
            ],
            heading="Column Headings",
        ),
        MultiFieldPanel(
            [
                InlinePanel("links", label="Footer Link"),
            ],
            heading="Links (assign each to Column 1 / 2 / 3)",
        ),
        MultiFieldPanel(
            [
                FieldPanel("about_text"),
            ],
            heading="About Text",
        ),
        MultiFieldPanel(
            [
                FieldPanel("footer_logo"),
                FieldPanel("logo_alt_text"),
                FieldPanel("copyright_text"),
            ],
            heading="Branding & Copyright",
        ),
        MultiFieldPanel(
            [
                FieldPanel("facebook_url"),
                FieldPanel("instagram_url"),
                FieldPanel("linkedin_url"),
                FieldPanel("youtube_url"),
                FieldPanel("tiktok_url"),
                FieldPanel("reddit_url"),
                FieldPanel("twitter_url"),
            ],
            heading="Social Media",
        ),
    ]

    class Meta:
        verbose_name = "Site Footer"
        verbose_name_plural = "Site Footers"

    def __str__(self):
        return "Site Footer"

    # ── Helpers used in the template ───────────────────────────────────────
    def col1_links(self):
        return self.links.filter(column="col1")

    def col2_links(self):
        return self.links.filter(column="col2")

    def col3_links(self):
        return self.links.filter(column="col3")


class HomePage(Page):

    max_count = 1

    def get_context(self, request):
        context = super().get_context(request)

        from articles.models import Article, ArticleIndexPage
        from issue.models import Issue, Topic, IssueIndexPage

        current_issue = Issue.objects.live().order_by('-date_of_publishing').first()
        context['current_issue'] = current_issue

        past_issues = Issue.objects.live()
        if current_issue:
            past_issues = past_issues.exclude(id=current_issue.id)

        context['past_issues'] = past_issues.order_by('-date_of_publishing')[:4]
        context['issue_index_page'] = IssueIndexPage.objects.live().first()

        issue_article_ids = []
        if current_issue:
            issue_articles = current_issue.get_all_articles()
            context['issue_articles'] = issue_articles

            same_topic_articles = []
            other_topic_articles = []
            if current_issue.topic:
                for a in issue_articles:
                    if a.topic == current_issue.topic:
                        same_topic_articles.append(a)
                    else:
                        other_topic_articles.append(a)
            else:
                same_topic_articles = issue_articles

            context['same_topic_articles'] = same_topic_articles
            context['other_topic_articles'] = other_topic_articles

            issue_article_ids = [a.id for a in issue_articles]
        else:
            context['issue_articles'] = []
            context['same_topic_articles'] = []
            context['other_topic_articles'] = []

        latest_articles = (
            Article.objects.live()
            .exclude(id__in=issue_article_ids)
            .order_by('-first_published_at')[:6]
        )
        context['latest_articles'] = latest_articles
        context['article_index_page'] = ArticleIndexPage.objects.live().first()

        return context


class CustomLogEntry(AbstractLogEntry):
    actor_roles = models.JSONField(default=list, blank=True, null=True)

    def save(self, *args, **kwargs):
        if not self.pk and self.actor:
            roles = []
            if self.actor.is_superuser:
                roles.append("Superuser")
            roles.extend(list(self.actor.groups.values_list('name', flat=True)))
            self.actor_roles = roles
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = 'Custom Log Entry'
        verbose_name_plural = 'Custom Log Entries'