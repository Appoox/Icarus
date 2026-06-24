from django.urls import path, reverse
from django.shortcuts import redirect
from django.contrib import messages
from django.utils.html import format_html
from django.templatetags.static import static
import django_filters
from wagtail import hooks
from wagtail.models import Page
from wagtail.snippets.views.snippets import SnippetViewSet, IndexView
from wagtail.admin.ui.tables import Column, BooleanColumn  # Import Column classes to enable column sorting
from wagtail.admin.widgets import HeaderButton
from .models import Comment, CommentNotificationPreference
from reader.models import ReaderUser  # Access custom user model fields for dropdown queries

# ---------------------------------------------------------------------------
# Custom ModelChoiceFilter that renders each option as the user's full name,
# falling back to their phone number if no name is set.
# ---------------------------------------------------------------------------
class CommenterFilter(django_filters.ModelChoiceFilter):
    def label_from_instance(self, obj):
        """
        Called for every <option> in the dropdown.
        Shows "Full Name (phone)" when a name exists, otherwise just the phone.
        """
        name = obj.get_full_name().strip()  # AbstractUser provides get_full_name()
        phone = str(obj.phone_number_encrypted) if obj.phone_number_encrypted else ''
        if name and phone:
            return f"{name} ({phone})"
        # Fall back to whichever piece of info is available
        return name or phone or str(obj)


# ---------------------------------------------------------------------------
# Custom ModelChoiceFilter that renders a Wagtail Page by its title.
# Used for both the Article (page) and Issue dropdowns.
# ---------------------------------------------------------------------------
class PageTitleFilter(django_filters.ModelChoiceFilter):
    def label_from_instance(self, obj):
        """Return the page title as the dropdown label."""
        return obj.title


# ---------------------------------------------------------------------------
# Filter set for the Comment list view.
# ---------------------------------------------------------------------------
class CommentFilterSet(django_filters.FilterSet):
    # ── Commenter filter ────────────────────────────────────────────────────
    user = CommenterFilter(
        field_name='user',
        label='Commenter',
        empty_label='All Commenters',
        queryset=lambda request: ReaderUser.objects.filter(
            comments__isnull=False  # Only surface users with active comments
        ).distinct().order_by('name'),
    )

    # ── Article filter ──────────────────────────────────────────────────────
    page = PageTitleFilter(
        field_name='page',
        label='Article',
        empty_label='All Articles',
        queryset=lambda request: Page.objects.filter(
            comments__isnull=False
        ).distinct().order_by('title'),
    )

    # ── Issue filter ────────────────────────────────────────────────────────
    issue = PageTitleFilter(
        field_name='issue',
        label='Issue',
        empty_label='All Issues',
        queryset=lambda request: Page.objects.filter(
            issue_comments__isnull=False
        ).distinct().order_by('title'),
    )

    # ── Approval & Removal filters ──────────────────────────────────────────
    # Swapped BooleanFilter for ChoiceFilter to properly process 'empty_label' without crashing
    is_approved = django_filters.ChoiceFilter(
        field_name='is_approved',
        choices=[(True, 'Yes'), (False, 'No')],
        empty_label='All'
    )
    is_removed = django_filters.ChoiceFilter(
        field_name='is_removed',
        choices=[(True, 'Yes'), (False, 'No')],
        empty_label='All'
    )

    class Meta:
        model = Comment
        fields = ['user', 'page', 'issue', 'is_approved', 'is_removed']


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


# 2. Custom Index View with Mute/Unmute Header Button and default sort order
class CommentIndexView(IndexView):

    def get_base_queryset(self):
        """
        Return comments sorted newest-first by default.
        Only applied when no explicit column header click ordering overrides it.
        """
        qs = super().get_base_queryset()
        if not self.request.GET.get('ordering'):
            qs = qs.order_by('-created_at')
        return qs

    def get_header_buttons(self):
        buttons = super().get_header_buttons()
        if self.request.user.is_authenticated:
            pref, created = CommentNotificationPreference.objects.get_or_create(user=self.request.user)
            label = 'Mute Email Notifications' if pref.receive_notifications else 'Unmute Email Notifications'
            icon = 'mail'
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
    # list_display_links = ['user_display']
    # ── Sortable Table Columns Configuration ───────────────────────────────
    # By mapping strings to Column objects containing database lookup paths in sort_key,
    # custom properties or model methods become fully sortable in the Wagtail dashboard.
    # list_display = [
    #     Column("user_display", label="User", sort_key="user__name"),
    #     Column("page_display", label="Article", sort_key="page__title"),
    #     Column("issue_display", label="Issue", sort_key="issue__title"),
    #     Column("body_truncated", label="Comment", sort_key="body"),
    #     BooleanColumn("is_approved", label="Approved", sort_key="is_approved"),
    #     BooleanColumn("is_removed", label="Removed", sort_key="is_removed"),
    #     Column("report_count", label="Reports"),  # Unsortable property calculation
    #     Column("created_at", label="Created At", sort_key="created_at"),
    # ]

    list_display = ["user", "page", "is_approved", "is_removed", "created_at"]
    
    # Attach our custom FilterSet for layout and empty label fallback parameters
    filterset_class = CommentFilterSet

    # Date filter widget initialization
    list_filter = ("created_at",)

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