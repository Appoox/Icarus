import django_filters
from django.urls import reverse
from django.utils import timezone
from wagtail import hooks
from wagtail.snippets.views.snippets import SnippetViewSet
from wagtail.admin.widgets import Button
from .models import Reader, PaymentDetails

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
        model = Reader
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

class ReaderSnippetViewSet(SnippetViewSet):
    model = Reader
    url_prefix = 'readers'
    menu_label = 'Readers'
    icon = 'user'
    menu_order = 300
    add_to_admin_menu = True
    
    list_display = ("name", "email", "subscription_plan", "subscription_end", "status_display")
    filterset_class = ReaderFilterSet
    search_fields = ("name", "email")

    def get_header_buttons(self, filter_parameters=None):
        buttons = super().get_header_buttons(filter_parameters)
        
        buttons.append(Button(
            'Print Subscriber List',
            url=reverse('print_subscribers'),
            classname='button button-secondary',
            icon_name='print',
            attrs={'target': '_blank'}
        ))
        
        return buttons

class PaymentDetailsSnippetViewSet(SnippetViewSet):
    model = PaymentDetails
    url_prefix = 'payments'
    menu_label = 'Payments'
    icon = 'credit-card'
    menu_order = 301
    add_to_admin_menu = True
    list_display = ("gateway_name", "amount", "status", "created_at")
    list_filter = ("status", "payment_method")

from wagtail.admin.menu import MenuItem

@hooks.register('register_admin_viewset')
def register_reader_viewsets():
    return [
        ReaderSnippetViewSet(),
        PaymentDetailsSnippetViewSet(),
    ]

@hooks.register('register_admin_menu_item')
def register_print_subscribers_menu_item():
    return MenuItem(
        'Print Subscriber List',
        reverse('print_subscribers'),
        icon_name='print',
        order=302,
        attrs={'target': '_blank'}
    )
