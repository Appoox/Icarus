from django.urls import path
from . import views

urlpatterns = [
    path('topics/<slug:slug>/', views.topic_detail, name='topic_detail'),
]
