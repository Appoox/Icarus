# literati/urls.py
# ─────────────────────────────────────────────────────────────────────────
# URL patterns for non-Wagtail views in the literati app.
#
# ⚠ Include this in your project-level urls.py:
#
#     from django.urls import path, include
#
#     urlpatterns = [
#         ...
#         path('', include('literati.urls')),
#         ...
#     ]
#
# The most_read_authors URL name is referenced by:
#   - footer_tags.py  → reverse('most_read_authors')
#   - DYNAMIC_URL_CHOICES in home/models.py  → 'most_read_author_url'
# ─────────────────────────────────────────────────────────────────────────

from django.urls import path
from . import views

urlpatterns = [
    # Rolling 30-day most-read authors ranked by article hit counts
    path('most-read/', views.most_read_authors, name='most_read_authors'),
]
