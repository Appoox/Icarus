from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_POST   
from django.utils import timezone                       
import uuid                                            
from the_librarian.views import superuser_required 

from .forms import ReaderProfileEditForm, UpdateInterestsForm
from .models import ReaderUser, PaymentDetails, PLANS


# def reader_signup(request):
#     """Register a new reader account."""
#     if request.user.is_authenticated:
#         return redirect('reader_profile')
# 
#     if request.method == 'POST':
#         form = ReaderSignupForm(request.POST)
#         if form.is_valid():
#             user = form.save()
#             login(request, user)
#             messages.success(request, 'Welcome! Your account has been created.')
#             return redirect('reader_profile')
#     else:
#         form = ReaderSignupForm()
# 
#     return render(request, 'reader/signup.html', {'form': form})


@login_required(login_url='/reader/login/')
def reader_profile(request):
    """Display reader profile with reading history and subscription info."""
    reader = request.user

    context = {
        'reader': reader,
        'read_articles': reader.read_articles.all().order_by('-first_published_at')[:20] if hasattr(reader.read_articles, 'all') else [],
        'interested_topics': reader.interested_topics.all() if hasattr(reader.interested_topics, 'all') else [],
        'all_topics': reader.interested_topics.all() if hasattr(reader.interested_topics, 'all') else [],
        'plans': PLANS,   # ✅ Pass plans so profile.html doesn't hardcode prices
    }
    return render(request, 'reader/profile.html', context)


@login_required(login_url='/reader/login/')
def edit_profile(request):
    """Let readers edit their profile."""
    reader = request.user

    if request.method == 'POST':
        form = ReaderProfileEditForm(request.POST, instance=reader)
        if form.is_valid():
            form.save()
            messages.success(request, 'Your profile has been updated.')
            return redirect('reader_profile')
    else:
        form = ReaderProfileEditForm(instance=reader)

    return render(request, 'reader/edit_profile.html', {'form': form})


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
        'idempotency_key': f'IK_{uuid.uuid4().hex}', # Generate a key for this specific session
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
    idempotency_key = request.POST.get('idempotency_key')

    # ✅ IDEMPOTENCY CHECK: If this key has already been processed, skip creation
    existing_payment = PaymentDetails.objects.filter(idempotency_key=idempotency_key).first()
    if existing_payment:
        messages.info(request, "Your payment is already being processed.")
        return redirect('reader_profile')

    # ✅ Use uuid for transaction IDs instead of timestamp (unique, not guessable)
    payment = PaymentDetails.objects.create(
        gateway_name='MockGateway',
        gateway_transaction_id=f'TXN_{uuid.uuid4().hex}',
        gateway_order_id=f'ORD_{uuid.uuid4().hex}',
        idempotency_key=idempotency_key,
        payment_method=method,
        amount=amount,
        status='completed',
    )

    try:
        reader = request.user
        reader.payment_details = payment
        reader.save(update_fields=['payment_details'])
        reader.activate_subscription(plan_type)
        messages.success(
            request,
            f"Successfully subscribed to the {plan['name']} plan!"
        )
    except Exception:
        messages.error(request, "Reader profile not found.")

    return redirect('reader_profile')


@login_required(login_url='/reader/login/')
@require_POST   # ✅ Cancellation must be a POST action, not a GET link
def cancel_subscription(request):
    """Cancel the reader's active subscription immediately."""
    try:
        reader = request.user
        if reader.is_subscribed:
            reader.subscription_plan = 'none'
            reader.subscription_end = timezone.now()
            reader.save(update_fields=['subscription_plan', 'subscription_end'])
            messages.success(request, "Your subscription has been cancelled.")
        else:
            messages.info(request, "You don't have an active subscription.")
    except Exception:
        messages.error(request, "Reader profile not found.")

    return redirect('reader_profile')


@login_required(login_url='/reader/login/')
def update_interests(request):
    """Let readers update their topic interests after signup."""
    try:
        reader = request.user
    except Exception:
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

@login_required(login_url='/reader/login/')
@require_POST
def toggle_favorite_article(request, article_id):
    """Toggle an article's favourite status for the logged-in reader."""
    from articles.models import Article
    article = get_object_or_404(Article, pk=article_id)

    try:
        reader = request.user
    except Exception:
        return JsonResponse({'error': 'Reader profile not found.'}, status=404)

    if reader.favorite_articles.filter(pk=article_id).exists():
        reader.favorite_articles.remove(article)
        favorited = False
    else:
        reader.favorite_articles.add(article)
        favorited = True

    return JsonResponse({'favorited': favorited, 'id': article_id, 'type': 'article'})


@login_required(login_url='/reader/login/')
@require_POST
def toggle_favorite_issue(request, issue_id):
    """Toggle an issue's favourite status for the logged-in reader."""
    from issue.models import Issue
    issue = get_object_or_404(Issue, pk=issue_id)

    try:
        reader = request.user
    except Exception:
        return JsonResponse({'error': 'Reader profile not found.'}, status=404)

    if reader.favorite_issues.filter(pk=issue_id).exists():
        reader.favorite_issues.remove(issue)
        favorited = False
    else:
        reader.favorite_issues.add(issue)
        favorited = True

    return JsonResponse({'favorited': favorited, 'id': issue_id, 'type': 'issue'})


# def reader_logout(request):
#     """Log the user out and redirect to home."""
#     logout(request)
#     messages.info(request, 'You have been signed out.')
#     return redirect('/')


@superuser_required
def print_subscribers(request):
    """
    View to display a printable list of all active or recently expired subscribers.
    Limited to staff members.
    """
    # Get all readers who have/had a plan, ordered by name
    subscribers = ReaderUser.objects.exclude(subscription_plan='none').order_by('name')
    
    # Optional: Filter for active only if requested, but usually admin wants the full list 
    # of anyone who ever paid/is paying. For now, let's show anyone with a plan.
    
    return render(request, 'reader/admin/print_subscribers.html', {
        'subscribers': subscribers,
        'now': timezone.now()
    })
@login_required(login_url='/reader/login/')
@require_POST
def deactivate_account(request):
    """
    Handle account deactivation request.
    This is a final action that logs the user out and disables the account.
    """
    reader = request.user
    
    # Optional: You could verify password here for extra security
    # if not reader.check_password(request.POST.get('password')):
    #     messages.error(request, "Incorrect password. Deactivation cancelled.")
    #     return redirect('reader_profile')

    reader.deactivate()
    logout(request)
    
    messages.warning(
        request, 
        "Your account has been deactivated. In accordance with our data retention policy, "
        "your personal data will be permanently purged after the statutory period."
    )
    return redirect('home')

import csv
from django.http import HttpResponse

@superuser_required
def export_mailing_list(request):
    """
    Generates a CSV mailing list for active print subscribers.
    Used for courier/postal delivery.
    """
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="icarus_mailing_list_{timezone.now().date()}.csv"'

    writer = csv.writer(response)
    # Header
    writer.writerow([
        'Name', 'Phone', 'Address Line 1', 'Address Line 2', 
        'Post Office', 'City', 'District', 'State', 'Pincode', 
        'Special Instructions'
    ])

    # Only get active print subscribers with a valid expiry date
    active_print_readers = ReaderUser.objects.filter(
        is_print_subscriber=True,
        print_delivery_status='active',
        print_expiry_date__gte=timezone.now().date()
    ).order_by('pincode', 'name')

    for reader in active_print_readers:
        writer.writerow([
            reader.name,
            str(reader.phone_number),
            reader.address_line_1,
            reader.address_line_2,
            reader.post_office,
            reader.city,
            reader.district,
            reader.state,
            reader.pincode,
            reader.delivery_notes
        ])

    return response
