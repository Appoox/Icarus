from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_POST   # ✅ NEW
from django.utils import timezone                       # ✅ NEW (fixes the bug)
import uuid                                             # ✅ NEW (better IDs than timestamp)

from .forms import ReaderSignupForm, UpdateInterestsForm
from .models import Reader, PaymentDetails

# ── Single source of truth for plan prices ──────────────────────────────────
# Previously hardcoded in both views.py and profile.html.
# Now defined once here and passed to templates as context.
PLANS = {
    '1_month':  {'name': '1 Month',   'price': 299},
    '3_months': {'name': '3 Months',  'price': 799},
    '6_months': {'name': '6 Months',  'price': 1499},
    '1_year':   {'name': '1 Year',    'price': 2499},
}


def reader_signup(request):
    """Register a new reader account."""
    if request.user.is_authenticated:
        return redirect('reader_profile')

    if request.method == 'POST':
        form = ReaderSignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, 'Welcome! Your account has been created.')
            return redirect('reader_profile')
    else:
        form = ReaderSignupForm()

    return render(request, 'reader/signup.html', {'form': form})


@login_required(login_url='/reader/login/')
def reader_profile(request):
    """Display reader profile with reading history and subscription info."""
    try:
        reader = request.user.reader
    except Reader.DoesNotExist:
        reader = Reader.objects.create(
            user=request.user,
            name=request.user.get_full_name() or request.user.username,
            email=request.user.email or '',
        )

    context = {
        'reader': reader,
        'read_articles': reader.read_articles.all().order_by('-first_published_at')[:20],
        'interested_topics': reader.interested_topics.all(),
        'all_topics': reader.interested_topics.all(),
        'plans': PLANS,   # ✅ Pass plans so profile.html doesn't hardcode prices
    }
    return render(request, 'reader/profile.html', context)


@login_required(login_url='/reader/login/')
def reader_checkout(request, plan_type):
    """Render the checkout page for a specific plan."""
    plan = PLANS.get(plan_type)   # ✅ Uses shared PLANS dict
    if not plan:
        messages.error(request, "Invalid subscription plan selected.")
        return redirect('reader_profile')

    return render(request, 'reader/checkout.html', {
        'plan_type': plan_type,
        'plan_name': plan['name'],
        'price': plan['price'],
    })


@login_required(login_url='/reader/login/')
@require_POST   # ✅ Blocks GET requests entirely — no more silent redirect fallback
def process_payment(request):
    """Simulate payment gateway callback and activate subscription."""
    plan_type = request.POST.get('plan_type')
    method = request.POST.get('payment_method', 'card')

    # ✅ Look up amount server-side — never trust the client's posted amount
    plan = PLANS.get(plan_type)
    if not plan:
        messages.error(request, "Invalid plan selected.")
        return redirect('reader_profile')

    amount = plan['price']

    # ✅ Use uuid for transaction IDs instead of timestamp (unique, not guessable)
    payment = PaymentDetails.objects.create(
        gateway_name='MockGateway',
        gateway_transaction_id=f'TXN_{uuid.uuid4().hex}',
        gateway_order_id=f'ORD_{uuid.uuid4().hex}',
        payment_method=method,
        amount=amount,
        status='completed',
    )

    try:
        reader = request.user.reader
        reader.payment_details = payment
        reader.save(update_fields=['payment_details'])
        reader.activate_subscription(plan_type)
        messages.success(
            request,
            f"Successfully subscribed to the {plan['name']} plan!"
        )
    except Reader.DoesNotExist:
        messages.error(request, "Reader profile not found.")

    return redirect('reader_profile')


@login_required(login_url='/reader/login/')
@require_POST   # ✅ Cancellation must be a POST action, not a GET link
def cancel_subscription(request):
    """Cancel the reader's active subscription immediately."""
    try:
        reader = request.user.reader
        if reader.is_subscribed:
            reader.subscription_plan = 'none'
            reader.subscription_end = timezone.now()
            reader.save(update_fields=['subscription_plan', 'subscription_end'])
            messages.success(request, "Your subscription has been cancelled.")
        else:
            messages.info(request, "You don't have an active subscription.")
    except Reader.DoesNotExist:
        messages.error(request, "Reader profile not found.")

    return redirect('reader_profile')


@login_required(login_url='/reader/login/')
def update_interests(request):
    """Let readers update their topic interests after signup."""
    try:
        reader = request.user.reader
    except Reader.DoesNotExist:
        return redirect('reader_profile')

    if request.method == 'POST':
        form = UpdateInterestsForm(request.POST, instance=reader)
        if form.is_valid():
            form.save()
            messages.success(request, "Your interests have been updated.")
            return redirect('reader_profile')
    else:
        form = UpdateInterestsForm(instance=reader)

    return render(request, 'reader/update_interests.html', {'form': form})


def reader_logout(request):
    """Log the user out and redirect to home."""
    logout(request)
    messages.info(request, 'You have been signed out.')
    return redirect('/')