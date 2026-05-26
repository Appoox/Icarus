import django_filters
from django.urls import reverse
from django.utils import timezone
from wagtail import hooks
from wagtail.snippets.views.snippets import SnippetViewSet, IndexView
from wagtail.admin.widgets import HeaderButton
from .models import ReaderUser, PaymentDetails, SubscriptionHistory
from django.urls import path
from django.shortcuts import render
from auditlog.models import LogEntry
from wagtail.admin.menu import MenuItem

class ReaderFilterSet(django_filters.FilterSet):
    sub_status = django_filters.ChoiceFilter(
        choices=(
            ('active', 'Active'),
            ('expired', 'Expired'),
            ('none', 'No Subscription'),
        ),
        method='filter_sub_status',
        label='Subscription Status'
    )

    class Meta:
        model = ReaderUser
        fields = ['subscription_plan', 'sub_status']

    def filter_sub_status(self, queryset, name, value):
        now = timezone.now()
        if value == 'active':
            return queryset.filter(subscription_end__gt=now).exclude(subscription_plan='none')
        if value == 'expired':
            return queryset.filter(subscription_end__lte=now).exclude(subscription_plan='none')
        if value == 'none':
            return queryset.filter(subscription_plan='none')
        return queryset

class ReaderIndexView(IndexView):
    def get_header_buttons(self):
        buttons = super().get_header_buttons()
        buttons.append(HeaderButton(
            label='Print Subscriber List',
            url=reverse('print_subscribers'),
            icon_name='print',
            classname='button button-secondary',
            attrs={'target': '_blank'}
        ))
        buttons.append(HeaderButton(
            label='Run Deactivation Purge (Anonymize)',
            url=reverse('admin_trigger_purge_deactivated'),
            icon_name='user-times',
            classname='button button-secondary',
        ))
        return buttons

class ReaderSnippetViewSet(SnippetViewSet):
    model = ReaderUser
    index_view_class = ReaderIndexView
    url_prefix = 'readers'
    menu_label = 'Readers'
    icon = 'user'
    menu_order = 300
    add_to_admin_menu = True
    
    list_display = ("name", "email", "phone_number", "subscription_plan", "subscription_end", "status_display")
    filterset_class = ReaderFilterSet
    search_fields = ("phone_number", "name", "email")

class PaymentDetailsSnippetViewSet(SnippetViewSet):
    model = PaymentDetails
    url_prefix = 'payments'
    menu_label = 'Payments'
    icon = 'credit-card'
    menu_order = 301
    add_to_admin_menu = True
    list_display = ("gateway_name", "amount", "status", "created_at")
    list_filter = ("status", "payment_method")

class SubscriptionHistoryFilterSet(django_filters.FilterSet):
    class Meta:
        model = SubscriptionHistory
        fields = ['subscription_plan', 'is_active', 'is_cancelled']

class SubscriptionHistoryIndexView(IndexView):
    def get_header_buttons(self):
        buttons = super().get_header_buttons()
        buttons.append(HeaderButton(
            label='Run 8-Year Expired Data Purge',
            url=reverse('admin_trigger_purge_expired'),
            icon_name='bin',
            classname='button button-danger',
        ))
        return buttons

class SubscriptionHistorySnippetViewSet(SnippetViewSet):
    model = SubscriptionHistory
    index_view_class = SubscriptionHistoryIndexView
    url_prefix = 'subscription-histories'
    menu_label = 'Subscription Histories'
    icon = 'history'
    menu_order = 302
    add_to_admin_menu = True
    
    list_display = ("reader", "subscription_plan", "subscription_start", "subscription_end", "is_active", "is_cancelled", "created_at")
    filterset_class = SubscriptionHistoryFilterSet
    search_fields = ("reader__name", "reader__phone_number", "reader__email")

@hooks.register('register_admin_viewset')
def register_reader_viewsets():
    return [
        ReaderSnippetViewSet(),
        PaymentDetailsSnippetViewSet(),
        SubscriptionHistorySnippetViewSet(),
    ]

# ── Custom User Forms ───────────────────────────────────────────────
from wagtail.users.forms import UserEditForm, UserCreationForm

class CustomUserEditForm(UserEditForm):
    pass # ReaderUser fields are already in panels if defined in the model

class CustomUserCreationForm(UserCreationForm):
    pass

@hooks.register('construct_user_edit_form')
def construct_user_edit_form(form, user, **kwargs):
    # This hook can be used to further customize the form if needed
    pass

# ── Audit Log Admin View ──────────────────────────────────────────────
def auditlog_view(request):
    entries = (
        LogEntry.objects
        .select_related("actor", "content_type")
        .order_by("-timestamp")[:200]
    )
    
    # Pre-process entries to safely get changes_display_dict
    safe_entries = []
    for entry in entries:
        try:
            entry.safe_changes = entry.changes_display_dict
        except Exception:
            entry.safe_changes = None
        safe_entries.append(entry)

    return render(request, "reader/auditlog_admin.html", {"entries": safe_entries})

@hooks.register("register_admin_urls")
def register_auditlog_url():
    from reader.views import admin_trigger_purge_deactivated, admin_trigger_purge_expired
    return [
        path("auditlog/", auditlog_view, name="auditlog_view"),
        path("purge-deactivated/", admin_trigger_purge_deactivated, name="admin_trigger_purge_deactivated"),
        path("purge-expired/", admin_trigger_purge_expired, name="admin_trigger_purge_expired"),
    ]

@hooks.register("register_admin_menu_item")
def register_auditlog_menu_item():
    return MenuItem(
        "Audit Log",
        reverse("auditlog_view"),
        icon_name="list-ul",
        order=900,
    )

