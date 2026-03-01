from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    path('signup/', views.reader_signup, name='reader_signup'),
    path('profile/', views.reader_profile, name='reader_profile'),
    path('logout/', views.reader_logout, name='reader_logout'),
    path('checkout/<str:plan_type>/', views.reader_checkout, name='reader_checkout'),
    path('process-payment/', views.process_payment, name='process_payment'),
    path(
        'login/',
        auth_views.LoginView.as_view(template_name='reader/login.html'),
        name='reader_login',
    ),
]
