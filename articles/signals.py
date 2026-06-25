# articles/signals.py
from django.dispatch import receiver
from wagtail.signals import page_published
from django_q.tasks import async_task
from .models import Article

@receiver(page_published, sender=Article)
def on_article_published(sender, instance, **kwargs):
    # Enqueue background processing using django-q2
    async_task("articles.tasks.generate_article_audio_task", instance.id)