from django.urls import path

from . import views

urlpatterns = [
    path('', views.index, name='glossary_index'),
    # No slug can contain a dot, so this never hides a term's page.
    path('terms.json', views.terms_json, name='glossary_terms'),
    path('<slug:slug>/', views.term_detail, name='glossary_term'),
]
