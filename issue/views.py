from django.shortcuts import render, get_object_or_404
from .models import Topic, Issue
from articles.models import Article

def topic_detail(request, slug):
    topic = get_object_or_404(Topic, slug=slug)
    # Get articles and issues for this topic
    articles = Article.objects.live().filter(topic=topic).order_by('-first_published_at')
    issues = Issue.objects.live().filter(topic=topic).order_by('-date_of_publishing')
    
    return render(request, 'issue/topic_detail.html', {
        'topic': topic,
        'articles': articles,
        'issues': issues,
    })
