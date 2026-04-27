from django.apps import AppConfig


class HomeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "home"

    def ready(self):
        from auditlog.registry import auditlog
        from .models import SiteHeader, SiteFooter, FooterLink, HomePage
        from wagtail.images.models import Image
        from wagtail.documents.models import Document
        
        auditlog.register(SiteHeader)
        auditlog.register(SiteFooter)
        auditlog.register(FooterLink)
        auditlog.register(HomePage)
        auditlog.register(Image)
        auditlog.register(Document)
