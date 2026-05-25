from django.urls import path, reverse
from django.shortcuts import redirect
from django.contrib import messages
from django.utils.html import format_html
from django.templatetags.static import static
from wagtail import hooks
from wagtail.snippets.views.snippets import SnippetViewSet, IndexView
from wagtail.admin.widgets import HeaderButton
from .models import Comment, CommentNotificationPreference

# 1. Custom URL/View for toggling notification preferences
def toggle_notifications_view(request):
    if not request.user.is_authenticated or not request.user.is_staff:
        return redirect('/admin/')
    
    pref, created = CommentNotificationPreference.objects.get_or_create(user=request.user)
    pref.receive_notifications = not pref.receive_notifications
    pref.save()
    
    status = "enabled" if pref.receive_notifications else "disabled"
    messages.success(request, f"Comment email notifications successfully {status}.")
    
    return redirect(reverse('wagtailsnippets_kalapila_comment:list'))

@hooks.register('register_admin_urls')
def register_comment_admin_urls():
    return [
        path('comments/notifications/toggle/', toggle_notifications_view, name='toggle_comment_notifications'),
    ]

# 2. Custom Index View with Mute/Unmute Header Button
class CommentIndexView(IndexView):
    def get_header_buttons(self):
        buttons = super().get_header_buttons()
        if self.request.user.is_authenticated:
            pref, created = CommentNotificationPreference.objects.get_or_create(user=self.request.user)
            label = 'Mute Email Notifications' if pref.receive_notifications else 'Unmute Email Notifications'
            icon = 'mail' # standard wagtail mail icon
            buttons.append(HeaderButton(
                label=label,
                url=reverse('toggle_comment_notifications'),
                icon_name=icon,
                classname='button button-secondary',
            ))
        return buttons

# 3. Snippet ViewSet for Comments
class CommentSnippetViewSet(SnippetViewSet):
    model = Comment
    index_view_class = CommentIndexView
    url_prefix = 'comments'
    menu_label = 'Comments'
    icon = 'comment'
    menu_order = 310
    add_to_admin_menu = True
    
    list_display = ("user_display", "page_display", "body_truncated", "is_approved", "is_removed", "report_count", "created_at")
    list_filter = ("created_at","is_approved", "is_removed")
    search_fields = ("body", "user__name", "page__title")

@hooks.register('register_admin_viewset')
def register_comment_viewset():
    return [
        CommentSnippetViewSet(),
    ]

# 4. Global Admin Injections for Real-Time Toast Notifications
@hooks.register('insert_global_admin_js')
def global_admin_js():
    return format_html('<script src="{}"></script>', static('js/admin_notifications.js'))

@hooks.register('insert_global_admin_css')
def global_admin_css():
    return format_html('<link rel="stylesheet" href="{}">', static('css/admin_notifications.css'))
