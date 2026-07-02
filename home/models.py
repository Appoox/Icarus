from django.db import models
from wagtail.models import Page, Orderable  # Orderable added for footer hierarchy
from wagtail.snippets.models import register_snippet
from modelcluster.fields import ParentalKey
from modelcluster.models import ClusterableModel
from wagtail.fields import RichTextField, StreamField
from wagtail.admin.panels import FieldPanel, InlinePanel, MultiFieldPanel

from auditlog.models import AbstractLogEntry
from wagtail.admin.panels import PageChooserPanel

from wagtail.contrib.settings.models import BaseGenericSetting, register_setting

from hitcount.models import HitCountMixin, HitCount
from django.contrib.contenttypes.fields import GenericRelation
from hitcount.views import HitCountMixin as HitCountViewMixin


@register_setting
class SiteContactSettings(BaseGenericSetting):
    contact_email = models.EmailField(blank=True)

    panels = [FieldPanel('contact_email')]

    class Meta:
        verbose_name = 'Contact Settings'

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


# ─────────────────────────────────────────────────────────────────────────────
#  Footer — three-level navigational hierarchy
#
#  SiteFooter
#    └── FooterSection  (Orderable + ClusterableModel)
#           • has a heading — e.g. "Explore", "Company", "Legal"
#           • one section  = one heading spanning however many columns below
#           └── FooterColumn  (Orderable + ClusterableModel)
#                  • headingless — heading is on the parent section
#                  • one column  = one vertical link list
#                  • add 1 column for the simple/standard layout
#                  • add 2+ columns to flow a long link list across multiple
#                    side-by-side lists under the same shared heading
#                  └── FooterColumnLink  (Orderable)
#                         • label + internal Wagtail page OR external URL
#
#  ⚠ Wagtail version note:
#    Three levels of nested InlinePanel are supported for ClusterableModel-
#    based snippets in Wagtail ≥ 5.x.  If you are on 4.x the inner-most
#    panel (column → links) may not render; upgrade to 5.x first.
#
#  ⚠ Migration note:
#    See data_migration.py for the full expand-migrate-contract sequence
#    that renames the old FooterColumn → FooterSection, inserts the new
#    FooterColumn, and repoints FooterColumnLink rows.
# ─────────────────────────────────────────────────────────────────────────────


class FooterSection(Orderable, ClusterableModel):
    """
    A navigational section in the site footer.

    One section = one heading + one or more columns underneath it.
    The most common use is one column per section (simple list).
    Add two or more columns when a long link list should flow side-by-side
    under the same shared heading.
    """

    footer = ParentalKey(
        "SiteFooter",
        on_delete=models.CASCADE,
        related_name="sections",
    )
    heading = models.CharField(
        max_length=100,
        blank=True,
        help_text="Section heading shown above the link column(s) — e.g. 'Explore', 'Company'.",
    )

    # Panels rendered inside SiteFooter's InlinePanel("sections")
    panels = [
        FieldPanel("heading"),
        InlinePanel(
            "columns",
            label="Column",
            help_text=(
                "One column = one vertical list of links. "
                "Add a second column to split a long list side-by-side under this heading."
            ),
        ),
    ]

    class Meta(Orderable.Meta):
        pass  # Inherits ordering = ['sort_order']

    def __str__(self):
        return self.heading or "(no heading)"


class FooterColumn(Orderable, ClusterableModel):
    """
    A single link column within a FooterSection.

    Headingless — the shared heading is on the parent FooterSection.
    Use the admin-only 'label' field to tell columns apart when a section
    has more than one (e.g. "Left", "Right").  The label is never
    rendered on the public site.
    """

    section = ParentalKey(
        "FooterSection",
        on_delete=models.CASCADE,
        related_name="columns",
    )
    # Admin-only label — helps editors identify columns in a multi-column section
    label = models.CharField(
        max_length=50,
        blank=True,
        help_text="Internal label (never shown on the site). Useful when this section has multiple columns.",
    )

    # Panels rendered inside FooterSection's InlinePanel("columns")
    panels = [
        FieldPanel("label"),
        InlinePanel("column_links", label="Link"),
    ]

    class Meta(Orderable.Meta):
        pass  # Inherits ordering = ['sort_order']

    def __str__(self):
        return self.label or "Column"


# Keys must match the keys returned in the context dict by get_site_footer()
# in footer_tags.py.  Adding a new dynamic destination = add a choice here
# AND a matching entry in the dynamic_url_map inside that tag function.
DYNAMIC_URL_CHOICES = [
    ('',                       '— Fixed page or URL (default) —'),
    # ── Issue destinations ──────────────────────────────────────────
    ('current_issue_url',      'Current Issue — updates when a new issue is published'),
    ('previous_issue_url',     'Previous Issue'),
    ('latest_issue_url',       'Issue Index: Latest'),
    ('most_read_issue_url',    'Issue Index: Most Read'),
    # ── Article destinations ────────────────────────────────────────
    ('latest_article_url',     'Article Index: Latest'),
    ('most_read_article_url',  'Article Index: Most Read'),
    # ── Topic / analytics destinations ─────────────────────────────
    ('most_read_topics_url',    'Most Read Topics — rolling 30-day most read'),
    ('most_read_authors_url',   'Most Read Authors — rolling 30-day most read'),
    ('archive_list_url',       'Archive'),
    ('postbox_url', 'Postbox')
]


class FooterColumnLink(Orderable):
    """
    A single navigational link within a FooterColumn.

    Destination priority (highest → lowest):
      1. dynamic_url_key  — resolves at render time via the template tag context
                            (e.g. always points to whichever issue is current)
      2. page             — internal Wagtail page; URL updates automatically if
                            the page slug changes
      3. url              — raw string; use for external links only

    The template tag (get_site_footer) injects the resolved URL as the private
    attribute _resolved_dynamic_url onto each link instance so that get_url()
    can return it without needing access to the request or template context.
    """

    column = ParentalKey(
        "FooterColumn",
        on_delete=models.CASCADE,
        related_name="column_links",
    )
    label = models.CharField(max_length=100)

    # ── Dynamic destination (auto-updating) ───────────────────────────
    dynamic_url_key = models.CharField(
        max_length=30,
        blank=True,
        default='',
        choices=DYNAMIC_URL_CHOICES,
        help_text=(
            "Choose a destination that updates automatically. "
            "If set, the Page and URL fields below are ignored."
        ),
    )

    # ── Fixed destinations ─────────────────────────────────────────────
    # Internal Wagtail page — URL auto-updates if the page slug changes
    page = models.ForeignKey(
        "wagtailcore.Page",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Pick an internal page. Takes priority over the URL field.",
    )
    # Fallback for external / absolute URLs (social pages, partner sites…)
    url = models.CharField(
        max_length=255,
        blank=True,
        help_text="External URL — used only when no internal page is chosen above.",
    )

    panels = [
        FieldPanel("label"),
        MultiFieldPanel(
            [FieldPanel("dynamic_url_key")],
            heading="Dynamic destination (auto-updating)",
        ),
        MultiFieldPanel(
            [PageChooserPanel("page"), FieldPanel("url")],
            heading="Fixed destination (ignored when a dynamic option is selected above)",
        ),
    ]

    class Meta(Orderable.Meta):
        pass  # Inherits ordering = ['sort_order']

    # URL schemes that are already absolute — never need a leading slash
    _ABSOLUTE_SCHEMES = ('/', 'http://', 'https://', 'mailto:', 'tel:', '#')

    def get_url(self):
        """
        Return the resolved URL.  Priority order (highest → lowest):

        1. _resolved_dynamic_url  — injected at render time by get_site_footer()
           in footer_tags.py when dynamic_url_key is set.  Always an internal
           Wagtail / Django-resolved URL, so no normalisation is needed.
        2. page.url               — slug-safe, always starts with '/'.
        3. url field              — normalised so that bare site-relative paths
           like 'trending/' become '/trending/', preventing browsers from
           interpreting them relative to the current page.
        """
        # Dynamic destination — URL injected by the footer template tag at render time
        resolved = getattr(self, '_resolved_dynamic_url', None)
        if self.dynamic_url_key and resolved:
            return resolved

        if self.page_id:
            return self.page.url  # always correct even if the page slug changes

        url = (self.url or '').strip()

        # Prepend '/' when the value looks like a bare site-relative path
        # (no scheme, no leading slash).  Genuine external URLs and anchors
        # are left untouched.
        if url and not url.startswith(self._ABSOLUTE_SCHEMES):
            url = '/' + url

        return url

    def is_external(self):
        """
        True only for genuine off-site links (http/https without page_id).
        Dynamic destinations always resolve to internal URLs — never external.
        Site-relative paths typed into the url field are also NOT external.
        """
        # Dynamic links always point to internal Wagtail / Django views
        if self.dynamic_url_key:
            return False
        if self.page_id:
            return False
        url = (self.url or '').strip()
        return url.startswith(('http://', 'https://'))

    def __str__(self):
        return self.label


@register_snippet
class SiteFooter(ClusterableModel):
    """
    Singleton-style snippet for the global site footer.
    Manage via Wagtail Admin → Snippets → Site Footer.

    Navigation is managed as Sections → Columns → Links.
    Add a section for each heading group.  Within a section, add one column
    for a simple list or multiple columns to split a long list side-by-side
    under the same heading.
    """

    # ── About / tagline text ───────────────────────────────────────────────
    about_text = models.TextField(
        blank=True,
        help_text="Descriptive paragraph shown below the link sections (e.g. editorial independence statement).",
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
    facebook_url  = models.URLField(blank=True, help_text="Full URL including https://")
    instagram_url = models.URLField(blank=True)
    linkedin_url  = models.URLField(blank=True)
    youtube_url   = models.URLField(blank=True)
    tiktok_url    = models.URLField(blank=True)
    reddit_url    = models.URLField(blank=True)
    twitter_url   = models.URLField(blank=True, help_text="X / Twitter URL")

    panels = [
        MultiFieldPanel(
            [
                InlinePanel(
                    "sections",
                    label="Section",
                    help_text=(
                        "Each section has a heading and one or more columns. "
                        "Use one column per section for the standard layout; "
                        "use multiple columns under one heading for wide link groups. "
                        "Drag the ⠿ handle to reorder."
                    ),
                ),
            ],
            heading="Link Sections",
        ),
        MultiFieldPanel(
            [FieldPanel("about_text")],
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


class HomePage(Page, HitCountMixin):

    hit_count_generic = GenericRelation(
        HitCount, object_id_field='object_pk',
        related_query_name='hit_count_generic_relation'
    )

    max_count = 1

    def get_context(self, request):
        context = super().get_context(request)

        if not (request.user.is_superuser or request.user.is_staff):
            hit_count = HitCount.objects.get_for_object(self)
            HitCountViewMixin().hit_count(request, hit_count)

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