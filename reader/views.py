from django.shortcuts import render, redirect
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.urls import reverse

from .forms import ReaderSignupForm
from .models import Reader, PaymentDetails


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
        # If a logged-in user has no reader profile, create one
        reader = Reader.objects.create(
            user=request.user,
            name=request.user.get_full_name() or request.user.username,
            email=request.user.email or '',
        )

    context = {
        'reader': reader,
        'read_articles': reader.read_articles.all().order_by('-first_published_at')[:20],
        'interested_topics': reader.interested_topics.all(),
    }
    return render(request, 'reader/profile.html', context)


@login_required(login_url='/reader/login/')
def reader_checkout(request, plan_type):
    """Render the checkout page for a specific plan."""
    plans = {
        '1_month': {'name': '1 Month', 'price': 299},
        '3_months': {'name': '3 Months', 'price': 799},
        '6_months': {'name': '6 Months', 'price': 1499},
        '1_year': {'name': '1 Year', 'price': 2499},
    }
    
    plan = plans.get(plan_type)
    if not plan:
        messages.error(request, "Invalid subscription plan selected.")
        return redirect('reader_profile')
        
    return render(request, 'reader/checkout.html', {
        'plan_type': plan_type,
        'plan_name': plan['name'],
        'price': plan['price'],
    })


@login_required(login_url='/reader/login/')
def process_payment(request):
    """Simulate payment gateway callback and activate subscription."""
    if request.method == 'POST':
        plan_type = request.POST.get('plan_type')
        amount = request.POST.get('amount')
        method = request.POST.get('payment_method', 'card')
        
        # 1. Create mock PaymentDetails
        payment = PaymentDetails.objects.create(
            gateway_name='MockGateway',
            gateway_transaction_id=f'TXN_{timezone.now().timestamp()}',
            gateway_order_id=f'ORD_{timezone.now().timestamp()}',
            payment_method=method,
            amount=amount,
            status='completed'
        )
        
        # 2. Update Reader
        try:
            reader = request.user.reader
            reader.payment_details = payment
            reader.activate_subscription(plan_type)
            messages.success(request, f"Successfully subscribed to the {plan_type.replace('_', ' ')} plan!")
        except Reader.DoesNotExist:
            messages.error(request, "Reader profile not found.")
            
        return redirect('reader_profile')
        
    return redirect('reader_profile')


def reader_logout(request):
    """Log the user out and redirect to home."""
    logout(request)
    messages.info(request, 'You have been signed out.')
    return redirect('/')
