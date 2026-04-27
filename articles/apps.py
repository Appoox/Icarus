from django.apps import AppConfig


class ArticlesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'articles'

    def ready(self):
        from auditlog.registry import auditlog
        from .models import Article, ArticleIndexPage
        auditlog.register(Article)
        auditlog.register(ArticleIndexPage)
