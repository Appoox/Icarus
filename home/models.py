from django.db import models

from wagtail.models import Page


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
