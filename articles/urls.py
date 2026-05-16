from django.urls import path
from . import views


urlpatterns = [
    path('track-read-fully/', views.track_read_fully, name='track_read_fully'),
    path('tags/<str:tag>/', views.tag_detail, name='tag_detail'),
    path('media/play/<int:doc_id>/', views.media_player, name='media_player'),
]
