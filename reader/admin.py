from django.contrib import admin
from .models import Reader, PaymentDetails


class PaymentDetailsInline(admin.StackedInline):
    model = PaymentDetails
    fk_name = 'reader'
    extra = 0
    can_delete = False

    # This trick lets us show PaymentDetails inline on the Reader admin.
    # Since it's a OneToOne the reverse relation is reader → payment_details.


@admin.register(Reader)
class ReaderAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'email', 'user', 'subscription_plan',
        'is_subscribed_display', 'subscription_end',
    )
    list_filter = ('subscription_plan',)
    search_fields = ('name', 'email', 'user__username')
    readonly_fields = ('is_subscribed_display',)
    filter_horizontal = ('read_articles', 'interested_topics')

    fieldsets = (
        (None, {
            'fields': ('name', 'email', 'phone_number', 'user'),
        }),
        ('Subscription', {
            'fields': (
                'subscription_plan', 'subscription_start',
                'subscription_end', 'is_subscribed_display',
            ),
        }),
        ('Payment', {
            'fields': ('payment_details',),
        }),
        ('Reading', {
            'fields': ('read_articles', 'interested_topics'),
        }),
    )

    @admin.display(boolean=True, description='Active?')
    def is_subscribed_display(self, obj):
        return obj.is_subscribed


@admin.register(PaymentDetails)
class PaymentDetailsAdmin(admin.ModelAdmin):
    list_display = (
        'gateway_name', 'gateway_transaction_id', 'payment_method',
        'amount', 'currency', 'status', 'created_at',
    )
    list_filter = ('status', 'gateway_name', 'payment_method')
    search_fields = ('gateway_transaction_id', 'gateway_order_id')
