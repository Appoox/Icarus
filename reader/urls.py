from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    path('signup/',          views.reader_signup,       name='reader_signup'),
    path('profile/',         views.reader_profile,      name='reader_profile'),
    path('logout/',          views.reader_logout,       name='reader_logout'),
    path('checkout/<str:plan_type>/', views.reader_checkout, name='reader_checkout'),
    path('process-payment/', views.process_payment,    name='process_payment'),

    # ✅ NEW
    path('cancel/',          views.cancel_subscription, name='cancel_subscription'),
    path('interests/',       views.update_interests,    name='update_interests'),

    path(
        'login/',
        auth_views.LoginView.as_view(template_name='reader/login.html'),
        name='reader_login',
    ),

    # ── Forgot Password ───────────────────────────────────────────────
    path(
        'password-reset/',
        auth_views.PasswordResetView.as_view(
            template_name='reader/password_reset_form.html',
            email_template_name='reader/password_reset_email.html',
            subject_template_name='reader/password_reset_subject.txt',
            success_url='/reader/password-reset/done/'
        ),
        name='reader_password_reset'
    ),
    path(
        'password-reset/done/',
        auth_views.PasswordResetDoneView.as_view(
            template_name='reader/password_reset_done.html'
        ),
        name='reader_password_reset_done'
    ),
    path(
        'reset/<uidb64>/<token>/',
        auth_views.PasswordResetConfirmView.as_view(
            template_name='reader/password_reset_confirm.html',
            success_url='/reader/reset/done/'
        ),
        name='reader_password_reset_confirm'
    ),
    path(
        'reset/done/',
        auth_views.PasswordResetCompleteView.as_view(
            template_name='reader/password_reset_complete.html'
        ),
        name='reader_password_reset_complete'
    ),

    # ── Change Password (Logged In) ──────────────────────────────────
    path(
        'password-change/',
        auth_views.PasswordChangeView.as_view(
            template_name='reader/password_change_form.html',
            success_url='/reader/password-change/done/'
        ),
        name='reader_password_change'
    ),
    path(
        'password-change/done/',
        auth_views.PasswordChangeDoneView.as_view(
            template_name='reader/password_change_done.html'
        ),
        name='reader_password_change_done'
    ),
]