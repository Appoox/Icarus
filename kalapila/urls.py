from django.urls import path
from . import views

app_name = 'kalapila'

urlpatterns = [
    path('post/', views.post_comment, name='post_comment'),
    path('<int:pk>/delete/', views.delete_comment, name='delete_comment'),
    path('<int:pk>/report/', views.report_comment, name='report_comment'),
    path('admin/notifications/', views.admin_notifications, name='admin_notifications'),
    path('admin/notifications/<int:pk>/read/', views.mark_notification_read, name='mark_notification_read'),
]
