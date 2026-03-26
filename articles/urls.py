from django.urls import path
from . import views


urlpatterns = [
    path('track-read-fully/', views.track_read_fully, name='track_read_fully'),
]
