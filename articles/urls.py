from django.urls import path
from . import views

app_name = 'articles'

urlpatterns = [
    path('track-read-fully/', views.track_read_fully, name='track_read_fully'),
]
