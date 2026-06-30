import django_filters
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.html import format_html
from django.templatetags.static import static

from wagtail import hooks
from wagtail.admin.widgets import HeaderButton
from wagtail.snippets.views.snippets import SnippetViewSet, IndexView

from .models import Postbox, PostboxNotificationPreference


# ── Filter set ────────────────────────────────────────────────────────────

class PostboxFilterSet(django_filters.FilterSet):
    feedback_type = django_filters.ChoiceFilter(
        choices=Postbox.FEEDBACK_TYPES,
        empty_label='All types',
    )
    is_reviewed = django_filters.ChoiceFilter(
        field_name='is_reviewed',
        choices=[(True, 'Reviewed'), (False, 'Unreviewed')],
        empty_label='All statuses',
    )
    rating = django_filters.ChoiceFilter(
        choices=[(i, '★' * i) for i in range(1, 6)],
        empty_label='Any rating',
    )

    class Meta:
        model  = Postbox
        fields = ['feedback_type', 'is_reviewed', 'rating']


# ── Custom index view ─────────────────────────────────────────────────────

class PostboxIndexView(IndexView):
    def get_base_queryset(self):
        qs = super().get_base_queryset()
        # Default: newest first, unless the user clicked a column header
        if not self.request.GET.get('ordering'):
            qs = qs.order_by('-submitted_at')
        return qs

    def get_header_buttons(self):
        buttons = super().get_header_buttons()
        if self.request.user.is_authenticated:
            pref, _ = PostboxNotificationPreference.objects.get_or_create(
                user=self.request.user
            )
            label = (
                'Mute Notifications'
                if pref.receive_notifications
                else 'Unmute Notifications'
            )
            buttons.append(HeaderButton(
                label=label,
                url=reverse('toggle_postbox_notifications'),
                icon_name='mail',
                classname='button button-secondary',
            ))
        return buttons


# ── Viewset ───────────────────────────────────────────────────────────────

class PostboxSnippetViewSet(SnippetViewSet):
    model             = Postbox
    index_view_class  = PostboxIndexView
    url_prefix        = 'postbox'
    menu_label        = 'Postbox'
    icon              = 'comment'
    menu_order        = 500
    add_to_admin_menu = True
    list_display      = [
        'feedback_type', 'truncated_feedback', 'stars',
        'user', 'submitted_at', 'is_reviewed',
    ]
    filterset_class = PostboxFilterSet
    list_filter     = ('submitted_at',)
    search_fields   = ['feedback', 'user__name']


# ── Notification preference toggle ────────────────────────────────────────

def toggle_postbox_notifications_view(request):
    if not (request.user.is_authenticated and request.user.is_staff):
        return redirect('/admin/')
    pref, _ = PostboxNotificationPreference.objects.get_or_create(
        user=request.user
    )
    pref.receive_notifications = not pref.receive_notifications
    pref.save()
    status = 'enabled' if pref.receive_notifications else 'disabled'
    messages.success(request, f'Postbox notifications {status}.')
    return redirect(reverse('wagtailsnippets_postbox_postbox:list'))


# ── Hook registrations ────────────────────────────────────────────────────

@hooks.register('register_admin_urls')
def register_postbox_admin_urls():
    return [
        path(
            'postbox/toggle-notifications/',
            toggle_postbox_notifications_view,
            name='toggle_postbox_notifications',
        ),
    ]


@hooks.register('register_admin_viewset')
def register_postbox_viewset():
    return [PostboxSnippetViewSet()]


@hooks.register('insert_global_admin_js')
def postbox_admin_js():
    return format_html(
        '<script src="{}"></script>',
        static('js/postbox_admin_notifications.js'),
    )