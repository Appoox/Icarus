from django.urls import path
from . import views

urlpatterns = [
    # Route for the rolling 30-day most read topics view dashboard
    path('topics/most-read/', views.most_read_topics, name='most_read_topics'),
    
    # Route tracking individual topic detail page configurations
    path('topics/<slug:slug>/', views.topic_detail, name='topic_detail'),
]
