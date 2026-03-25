from wagtail import hooks
from wagtail.admin.viewsets.pages import PageListingViewSet
from articles.wagtail_hooks import HitCountColumn, AnalyticsColumn

class LiteratiPageListingViewSet(PageListingViewSet):
    icon = 'user'
    menu_label = 'Authors'
    menu_order = 201
    add_to_admin_menu = True

    @property
    def columns(self):
        return super().columns + [
            HitCountColumn("hit_count", label="Views", sort_key="hit_count_generic__hits"),
            AnalyticsColumn("read_fully_count", "read_fully_count", label="Read Fully", sort_key="read_fully_count")
        ]

@hooks.register('register_admin_viewset')
def register_literati_viewset():
    from .models import Literati
    LiteratiPageListingViewSet.model = Literati
    return LiteratiPageListingViewSet('literati')
