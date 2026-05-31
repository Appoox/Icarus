# views.py
# ─────────────────────────────────────────────────────────────────────────
# Application views handling presentation tracking logic for Topics
# ─────────────────────────────────────────────────────────────────────────

from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from datetime import timedelta
from django.db.models import Count
from django.contrib.contenttypes.models import ContentType
from django.apps import apps
from hitcount.models import Hit
from wagtail.models import Page as WagtailPage
from collections import defaultdict

from .models import Topic, Issue
from articles.models import Article

def topic_detail(request, slug):
    """Renders the individual archive directory listing for a single topic snippet."""
    topic = get_object_or_404(Topic, slug=slug)
    # Get articles and issues for this topic
    articles = Article.objects.live().filter(topic=topic).order_by('-first_published_at')
    issues = Issue.objects.live().filter(topic=topic).order_by('-date_of_publishing')
    
    return render(request, 'issue/topic_detail.html', {
        'topic': topic,
        'articles': articles,
        'issues': issues,
    })

def most_read_topics(request):
    """
    Calculates and displays the most read topics over a rolling 30-day window
    by aggregating hit count metrics from all live articles belonging to each topic.
    Also fetches and attaches the most-read articles within each topic for previewing.
    """
    one_month_ago = timezone.now() - timedelta(days=30)
    
    # Safely obtain content types for generic hit relationship mapping
    try:
        Article_model = apps.get_model('articles', 'Article')
        article_ct = ContentType.objects.get_for_model(Article_model)
        page_ct = ContentType.objects.get_for_model(WagtailPage)
    except LookupError:
        return render(request, 'issue/most_read_topics.html', {'topics': []})

    # Pull live articles to establish an ID-to-Topic relational mapping reference
    live_articles = Article_model.objects.live().values('id', 'topic_id')
    live_pks = [str(a['id']) for a in live_articles]
    article_to_topic_map = {a['id']: a['topic_id'] for a in live_articles if a['topic_id']}

    # Aggregate total traffic views grouped by unique article ID string references
    top_hits = (
        Hit.objects.filter(
            created__gte=one_month_ago,
            hitcount__content_type__in=[article_ct, page_ct],
            hitcount__object_pk__in=live_pks,
        )
        .values('hitcount__object_pk')
        .annotate(total_views=Count('id'))
    )

    # Correlate individual article views into direct maps
    topic_views_map = {}
    article_views_map = {}
    
    for h in top_hits:
        try:
            article_id = int(h['hitcount__object_pk'])
            views_count = h['total_views']
            
            # Map views directly to the article ID
            article_views_map[article_id] = views_count
            
            # Aggregate views upward into the parent topic counter
            topic_id = article_to_topic_map.get(article_id)
            if topic_id:
                topic_views_map[topic_id] = topic_views_map.get(topic_id, 0) + views_count
        except (ValueError, TypeError):
            pass

    # Query all active live articles to map them down to parent topics for rendering
    all_live_articles = Article_model.objects.live()
    topic_to_articles_group = defaultdict(list)
    
    for article in all_live_articles:
        if article.topic_id:
            # Assign recent view metrics onto individual objects
            article.views_30_days = article_views_map.get(article.id, 0)
            topic_to_articles_group[article.topic_id].append(article)

    # Assign dynamic parameters into all active Topic model objects
    topics = list(Topic.objects.all())
    for topic in topics:
        topic.total_views = topic_views_map.get(topic.id, 0)
        
        # Pull associated articles, sort by views, and isolate top entries for preview panel
        associated_articles = topic_to_articles_group.get(topic.id, [])
        associated_articles.sort(key=lambda x: x.views_30_days, reverse=True)
        topic.top_articles = associated_articles[:5]

    # Sort items sequentially based on highest cumulative reader engagement
    topics.sort(key=lambda x: x.total_views, reverse=True)

    return render(request, 'issue/most_read_topics.html', {
        'topics': topics,
    })