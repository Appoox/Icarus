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
from django.templatetags.static import static
from django.utils.html import format_html

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

    def search_queryset(self, queryset):
        """
        Intercepts Wagtail's search workflow to allow looking up readers by their 
        exact phone number using a deterministic blind index hash strategy.
        """
        # Retrieve and clean the query string input by the administrator
        search_query = self.search_query.strip() if self.search_query else ""
        
        if search_query:
            # Check if the query resembles a phone number string (has digits or starts with +)
            if search_query.startswith('+') or any(char.isdigit() for char in search_query):
                # Calculate the exact SHA-256 blind index hash via the model's helper method
                hashed_phone = ReaderUser.hash_phone(search_query)
                from django.db.models import Q
                
                # Perform a direct ORM evaluation bypassing full-text search constraints
                return queryset.filter(
                    Q(name__icontains=search_query) |
                    Q(email__icontains=search_query) |
                    Q(phone_number_hash=hashed_phone)
                )
                
        # Default back to standard text-search indexing for ordinary word queries
        return super().search_queryset(queryset)

class ReaderSnippetViewSet(SnippetViewSet):
    model = ReaderUser
    index_view_class = ReaderIndexView
    url_prefix = 'readers'
    menu_label = 'Readers'
    icon = 'group'
    menu_order = 300
    add_to_admin_menu = True
    
    # phone_display is a model method that decrypts on access — Wagtail calls it as a callable.
    list_display = ("name", "email", "phone_display", "subscription_plan", "subscription_end", "status_display")
    filterset_class = ReaderFilterSet
    # phone_number removed: encrypted fields can't be searched at DB level.
    # Admins can find readers by name or email.
    search_fields = ("name", "email")

class PaymentDetailsSnippetViewSet(SnippetViewSet):
    model = PaymentDetails
    url_prefix = 'payments'
    menu_label = 'Payments'
    icon = 'tag'
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
    search_fields = ("reader__name", "reader__email")

@hooks.register('register_admin_viewset')
def register_reader_viewsets():
    return [
        ReaderSnippetViewSet(),
        PaymentDetailsSnippetViewSet(),
        SubscriptionHistorySnippetViewSet(),
    ]

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


@hooks.register('insert_global_admin_css')
def global_admin_css():
    """
    Injects toast styles into the Wagtail admin.

    The critical hide-rule is inlined directly so it takes effect even if
    the external CSS file is not yet collected / served.  The inline rule
    hides Wagtail's native message container immediately on paint, before
    any JS runs, preventing the yellow banner from ever flashing.
    The external file provides the full toast visual styles on top.
    """
    css_url = static('css/admin_toasts.css')
    return format_html(
        # External file: full toast styles (colours, animation, close button)
        '<link rel="stylesheet" href="{}">'
        # Inline fallback: the one rule that MUST be present even if the file
        # is missing (e.g. collectstatic not yet run in production).
        '<style>'
        '[data-w-messages-target="container"]{{display:none!important}}'
        '</style>',
        css_url,
    )


@hooks.register('insert_global_admin_js')
def global_admin_js():
    """
    Injects the toast JS into the Wagtail admin.

    A small inline bootstrap script runs immediately and sets up the
    MutationObserver so it is watching before Wagtail's own Stimulus
    controllers initialise.  The external file contains the full logic.
    """
    js_url = static('js/admin_toasts.js') + '?v=' + str(int(timezone.now().timestamp()))
    return format_html(
        # Inline bootstrap: confirm hook is alive, then load the full script
        '<script>'
        'console.log("[admin_toasts] hook injected");'
        '</script>'
        '<script src="{}" defer></script>',
        js_url,
    )