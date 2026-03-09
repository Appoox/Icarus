from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta

from wagtail.admin.panels import FieldPanel, MultiFieldPanel, FieldRowPanel
from wagtail.snippets.models import register_snippet
from wagtail.search import index


@register_snippet
class Reader(index.Indexed, models.Model):
    """
    Reader profile linked to a Django User. Tracks subscription status,
    payment details, reading history, and topic interests.
    """

    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    phone_number = models.CharField(max_length=20, blank=True)
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='reader',
    )

    search_fields = [
        index.SearchField('name'),
        index.SearchField('email'),
    ]

    # ── Subscription ──────────────────────────────────────────────────
    SUBSCRIPTION_PLANS = [
        ('none', 'No Subscription'),
        ('1_month', '1 Month'),
        ('3_months', '3 Months'),
        ('6_months', '6 Months'),
        ('1_year', '1 Year'),
    ]

    PLAN_DURATIONS = {
        'none': timedelta(days=0),
        '1_month': timedelta(days=30),
        '3_months': timedelta(days=90),
        '6_months': timedelta(days=180),
        '1_year': timedelta(days=365),
    }

    subscription_plan = models.CharField(
        max_length=20,
        choices=SUBSCRIPTION_PLANS,
        default='none',
        help_text='Active subscription plan.',
    )
    subscription_start = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When the current subscription period began.',
    )
    subscription_end = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When the current subscription period expires.',
    )

    @property
    def is_subscribed(self):
        """True if the reader has an active, non-expired subscription."""
        if self.subscription_plan == 'none':
            return False
        if self.subscription_end is None:
            return False
        return timezone.now() < self.subscription_end

    def activate_subscription(self, plan):
        """Start or renew a subscription with the given plan key."""
        duration = self.PLAN_DURATIONS.get(plan)
        if not duration or plan == 'none':
            return
        now = timezone.now()
        self.subscription_plan = plan
        self.subscription_start = now
        self.subscription_end = now + duration
        self.save(update_fields=[
            'subscription_plan', 'subscription_start', 'subscription_end',
        ])

    # ── Payment ───────────────────────────────────────────────────────
    payment_details = models.OneToOneField(
        'PaymentDetails',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='reader',
    )

    # ── Reading History & Interests ───────────────────────────────────
    read_articles = models.ManyToManyField(
        'articles.Article',
        blank=True,
        related_name='readers',
        help_text='Articles this reader has accessed.',
    )
    interested_topics = models.ManyToManyField(
        'issue.Topic',
        blank=True,
        related_name='interested_readers',
        help_text="Topics this reader is interested in.",
    )

    panels = [
        MultiFieldPanel([
            FieldPanel('name'),
            FieldPanel('email'),
            FieldPanel('phone_number'),
            FieldPanel('user'),
        ], heading="Personal Information"),
        MultiFieldPanel([
            FieldPanel('subscription_plan'),
            FieldRowPanel([
                FieldPanel('subscription_start'),
                FieldPanel('subscription_end'),
            ]),
        ], heading="Subscription Status"),
        FieldPanel('payment_details'),
        MultiFieldPanel([
            FieldPanel('read_articles'),
            FieldPanel('interested_topics'),
        ], heading="Activity & Interests"),
    ]

    class Meta:
        verbose_name = 'Reader'
        verbose_name_plural = 'Readers'

    def __str__(self):
        return f'{self.name} ({self.email})'


@register_snippet
class PaymentDetails(models.Model):
    """
    Structured payment information, designed for future integration
    with gateways like Cashfree or BillDesk.
    """

    PAYMENT_METHODS = [
        ('card', 'Credit / Debit Card'),
        ('upi', 'UPI'),
        ('netbanking', 'Net Banking'),
        ('wallet', 'Wallet'),
        ('other', 'Other'),
    ]

    PAYMENT_STATUSES = [
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('refunded', 'Refunded'),
    ]

    # Gateway reference
    gateway_name = models.CharField(
        max_length=50,
        blank=True,
        help_text='Payment gateway used, e.g. "cashfree", "billdesk".',
    )
    gateway_transaction_id = models.CharField(
        max_length=255,
        blank=True,
        help_text='Transaction ID returned by the payment gateway.',
    )
    gateway_order_id = models.CharField(
        max_length=255,
        blank=True,
        help_text='Order ID sent to the payment gateway.',
    )

    # Payment info
    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_METHODS,
        default='card',
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
    )
    currency = models.CharField(max_length=3, default='INR')
    status = models.CharField(
        max_length=20,
        choices=PAYMENT_STATUSES,
        default='pending',
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    panels = [
        MultiFieldPanel([
            FieldPanel('gateway_name'),
            FieldPanel('gateway_transaction_id'),
            FieldPanel('gateway_order_id'),
        ], heading="Gateway Reference"),
        MultiFieldPanel([
            FieldPanel('payment_method'),
            FieldRowPanel([
                FieldPanel('amount'),
                FieldPanel('currency'),
            ]),
            FieldPanel('status'),
        ], heading="Payment Info"),
        MultiFieldPanel([
            FieldPanel('created_at', read_only=True),
            FieldPanel('updated_at', read_only=True),
        ], heading="Timestamps"),
    ]

    class Meta:
        verbose_name = 'Payment Details'
        verbose_name_plural = 'Payment Details'

    def __str__(self):
        return (
            f'{self.gateway_name or "—"} · {self.get_status_display()} '
            f'· ₹{self.amount}'
        )
