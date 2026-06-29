from django.urls import path
from . import views

app_name = 'postbox'

urlpatterns = [
    path('', views.postbox_page, name='page'),
]