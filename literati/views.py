# literati/views.py
# ─────────────────────────────────────────────────────────────────────────
# Views for the Literati / Author section of Icarus.
# ─────────────────────────────────────────────────────────────────────────

from django.shortcuts import render
from django.utils import timezone
from datetime import timedelta
from django.db.models import Count
from django.contrib.contenttypes.models import ContentType
from django.apps import apps
from hitcount.models import Hit
from wagtail.models import Page as WagtailPage
from collections import defaultdict
# Import Django's structural paginator classes to handle page distribution splits
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

from .models import Literati, ArticleAuthorRelationship


def most_read_authors(request):
    """
    Calculates and displays the most-read authors over a rolling 30-day window
    by aggregating hit count metrics from all live articles that carry an
    ArticleAuthorRelationship pointing to each Literati author page.

    The approach mirrors issue.views.trending_topics exactly:
      1. Collect live article IDs and build an article → [author_ids] map.
      2. Query Hit rows for those articles in the 30-day window.
      3. Walk the hits back through the map to accumulate per-author totals.
      4. Annotate Literati objects with .total_views and sort descending.

    Context passed to template:
      page_obj — Paginator page instance referencing the current active slice 
                 of 10 authors, each with .total_views and pre-sorted .top_articles
    """
    one_month_ago = timezone.now() - timedelta(days=30)

    # ── Resolve content types for the Hit filter ──────────────────────────
    try:
        Article_model = apps.get_model('articles', 'Article')
        article_ct    = ContentType.objects.get_for_model(Article_model)
        page_ct       = ContentType.objects.get_for_model(WagtailPage)
    except LookupError:
        # Graceful degradation if articles app is unavailable
        return render(request, 'literati/most_read_authors.html', {'page_obj': None})

    # ── Build article → [author_id, …] mapping ────────────────────────────
    # Only consider live articles to avoid counting hits on unpublished drafts.
    live_articles = Article_model.objects.live()
    live_article_ids = set(live_articles.values_list('id', flat=True))
    live_pks_str     = [str(pk) for pk in live_article_ids]

    article_to_authors = {}
    for rel in (ArticleAuthorRelationship.objects
                .filter(page_id__in=live_article_ids)
                .values('page_id', 'author_id')):
        # One article can have multiple authors — accumulate into a list
        article_to_authors.setdefault(rel['page_id'], []).append(rel['author_id'])

    # ── Aggregate hit counts per article in the rolling 30-day window ─────
    top_hits = (
        Hit.objects.filter(
            created__gte=one_month_ago,
            hitcount__content_type__in=[article_ct, page_ct],
            hitcount__object_pk__in=live_pks_str,
        )
        .values('hitcount__object_pk')
        .annotate(total_views=Count('id'))
    )

    # Create a fast-lookup dict mapping article ID to its 30-day view count
    article_views_map = {}
    for h in top_hits:
        try:
            article_views_map[int(h['hitcount__object_pk'])] = h['total_views']
        except (ValueError, TypeError):
            pass

    # ── Correlate article views back to their authors in memory ───────────
    author_views_map = {}
    author_to_articles_group = defaultdict(list)

    for article in live_articles:
        # Assign recent view metrics onto individual article objects
        views_count = article_views_map.get(article.id, 0)
        article.views_30_days = views_count
        
        # Pull authors associated with this article
        authors_for_art = article_to_authors.get(article.id, [])
        for author_id in authors_for_art:
            # Accumulate overall views for the author's aggregate metric score
            author_views_map[author_id] = author_views_map.get(author_id, 0) + views_count
            # Append article to author's portfolio list for preview aggregation
            author_to_articles_group[author_id].append(article)

    # ── Annotate Literati objects with total_views and sort ───────────────
    # select_related('profile_image') avoids an extra query per thumbnail
    # in the template's {% image %} tag.
    authors = list(Literati.objects.live().select_related('profile_image'))
    for author in authors:
        author.total_views = author_views_map.get(author.id, 0)
        
        # Pull associated articles, sort by views, and isolate top 5 entries for preview panel
        associated_articles = author_to_articles_group.get(author.id, [])
        associated_articles.sort(key=lambda x: x.views_30_days, reverse=True)
        author.top_articles = associated_articles[:5]

    # Keep only authors whose articles were actually read; sort highest first
    authors = sorted(
        (a for a in authors if a.total_views > 0),
        key=lambda a: a.total_views,
        reverse=True,
    )

    # Instantiate core Paginator container, enforcing a limit of 10 items per page block
    paginator = Paginator(authors, 10)
    page_request = request.GET.get('page')
    
    try:
        page_obj = paginator.page(page_request)
    except PageNotAnInteger:
        # If page parameter is missing or non-numeric, fallback smoothly onto the first slice page
        page_obj = paginator.page(1)
    except EmptyPage:
        # If target parameter breaks upper limits, drop context safely into the final page window
        page_obj = paginator.page(paginator.num_pages)

    return render(request, 'literati/most_read_authors.html', {'page_obj': page_obj})