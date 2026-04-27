import django_filters
from django.urls import reverse
from django.utils import timezone
from wagtail import hooks
from wagtail.snippets.views.snippets import SnippetViewSet, IndexView
from wagtail.admin.widgets import HeaderButton
from .models import ReaderUser, PaymentDetails
from wagtail.admin.forms.auth import LoginForm
from django import forms
from phonenumber_field.formfields import SplitPhoneNumberField
from django.urls import path
from django.shortcuts import render
from auditlog.models import LogEntry
from wagtail.admin.menu import MenuItem
from wagtail.admin.ui.components import Component

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

@hooks.register('register_admin_viewset')
def register_reader_viewsets():
    return [
        ReaderSnippetViewSet(),
        PaymentDetailsSnippetViewSet(),
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
    return render(request, "reader/auditlog_admin.html", {"entries": entries})

@hooks.register("register_admin_urls")
def register_auditlog_url():
    return [
        path("auditlog/", auditlog_view, name="auditlog_view"),
    ]

@hooks.register("register_admin_menu_item")
def register_auditlog_menu_item():
    return MenuItem(
        "Audit Log",
        reverse("auditlog_view"),
        icon_name="list-ul",
        order=900,
    )

# ── Dashboard Audit Log Panel ─────────────────────────────────────────

class AuditLogPanel(Component):
    order = 300
    template_name = "reader/auditlog_panel.html"

    def get_context_data(self, parent_context):
        return {
            "entries": LogEntry.objects.select_related("actor", "content_type").order_by("-timestamp")[:10],
        }

@hooks.register('construct_homepage_panels')
def add_audit_log_panel(request, panels):
    # Remove the default "Recent Activity" or "Recent Edits" panels if they exist
    # The default activity panel often has an order around 300.
    # We will remove any panel that looks like it might be the activity panel
    # and add ours instead.
    
    # In recent Wagtail, 'panels' is a list of Component objects.
    # We can try to identify them by their class name if possible, 
    # but since it varies by Wagtail version, we'll just add ours 
    # and maybe filter out others if we can identify them.
    
    # Common activity panel class names in Wagtail:
    # - RecentEditsPanel
    # - PagesForModerationPanel
    
    new_panels = []
    for panel in panels:
        # Keep everything except what might be the default activity/edits panels
        # Usually we want to keep SiteSummaryPanel (order 100)
        class_name = panel.__class__.__name__
        if class_name not in ['RecentEditsPanel', 'PagesForModerationPanel']:
             new_panels.append(panel)
    
    # Clear and update the list
    panels[:] = new_panels
    panels.append(AuditLogPanel())
