from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.core.paginator import Paginator
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import Http404, JsonResponse
from django.db import IntegrityError, transaction
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils import timezone                       
import logging
import uuid
from the_librarian.views import superuser_required

logger = logging.getLogger(__name__)

# Session key holding {idempotency_key: plan_type} for checkouts this browser
# session has opened. A payment is only accepted for a key issued here, so a
# client can neither omit the key nor invent one.
_CHECKOUT_KEYS = 'checkout_idempotency_keys'
_MAX_OPEN_CHECKOUTS = 5

from .forms import ReaderProfileEditForm, UpdateInterestsForm
from .models import ReaderUser, PaymentDetails, PLANS
from django.conf import settings
from django.utils.translation import gettext as _

from allauth.account.views import SignupView


class ReaderSignupView(SignupView):
    """
    allauth's SignupView with one extra behaviour: an under-18 refusal from
    CustomSignupForm.clean() (ValidationError code='underage') redirects to
    the full-screen guardian explainer page instead of re-rendering the form
    with an inline error.

    Privacy property: the redirect discards the refused POST entirely — the
    minor's birth year is neither echoed in the URL nor stored anywhere,
    preserving the "persist nothing from a refused signup" guarantee.

    Wired in Icarus/urls.py as a path("accounts/signup/") entry placed BEFORE
    include("allauth.urls"), which shadows allauth's own signup route.
    """

    def form_invalid(self, form):
        if any(e.code == 'underage' for e in form.non_field_errors().as_data()):
            return redirect('guardian_account_info')
        return super().form_invalid(form)


def guardian_account_info(request):
    """
    Full-screen explainer shown when an under-18 birth year is entered at
    signup: minors should not create their own account; a guardian or parent
    can open a household account everyone at home reads on.  Served as 200 —
    real, linkable content (also linked from the signup form's help text),
    deliberately unlike the under_construction soft-404.
    """
    return render(request, 'account/guardian_signup.html')


@login_required(login_url=settings.LOGIN_URL)
def reader_profile(request):
    """Display reader profile with reading history and subscription info."""
    reader = request.user

    comments_qs = reader.comments.select_related('page').order_by('-created_at')
    paginator = Paginator(comments_qs, 10)
    own_comments = paginator.get_page(request.GET.get('comments_page'))

    # Fetch subscription history if any
    subscription_history = []
    if hasattr(reader, 'subscription_history_records'):
        subscription_history = reader.subscription_history_records.all().order_by('-subscription_start')

    context = {
        'reader': reader,
        # Only surface read history when tracking consent is active.
        # When consent is off (read_history_consent_at is None), pass an empty list
        # so the template's "tracking disabled" branch renders instead of stale data
        # that may have accumulated before revocation or via a direct .add() bypass.
        'read_articles': reader.read_articles.all().order_by('-first_published_at')[:20] if reader.tracking_consent else [],
        'interested_topics': reader.interested_topics.all() if hasattr(reader.interested_topics, 'all') else [],
        'all_topics': reader.interested_topics.all() if hasattr(reader.interested_topics, 'all') else [],
        'plans': PLANS,
        'own_comments': own_comments,
        'subscription_history': subscription_history,
    }
    return render(request, 'reader/profile.html', context)


@login_required(login_url=settings.LOGIN_URL)
def edit_profile(request):
    """Let readers edit their profile."""
    reader = request.user

    if request.method == 'POST':
        form = ReaderProfileEditForm(request.POST, request.FILES, instance=reader)
        if form.is_valid():
            form.save()
            messages.success(request, _('നിങ്ങളുടെ പ്രൊഫൈൽ പുതുക്കി.'))
            return redirect('reader_profile')
    else:
        form = ReaderProfileEditForm(instance=reader)

    return render(request, 'reader/edit_profile.html', {'form': form})


@csrf_exempt  # mail clients' one-click POST carries no CSRF token; the signed token is the credential
def newsletter_unsubscribe(request, token):
    """
    One-click newsletter unsubscribe from the link in every newsletter.

    GET shows a confirmation button (link scanners and previews only GET,
    so they can't unsubscribe anyone); POST unsubscribes. Mail clients that
    support RFC 8058 POST here directly via the List-Unsubscribe header.
    No login needed: the URL carries a signed reader id.
    """
    from .newsletter import reader_pk_from_token

    reader_pk = reader_pk_from_token(token)
    reader = ReaderUser.objects.filter(pk=reader_pk).first() if reader_pk else None
    if reader is None:
        raise Http404()

    done = False
    if request.method == 'POST':
        if reader.newsletter_opt_in:
            reader.newsletter_opt_in = False
            reader.save(update_fields=['newsletter_opt_in', 'newsletter_opt_in_at'])
        done = True
    return render(request, 'reader/newsletter_unsubscribe.html', {'done': done})


@login_required(login_url=settings.LOGIN_URL)
def reader_checkout(request, plan_type):
    """Render the checkout page for a specific plan."""
    plan = PLANS.get(plan_type)   # ✅ Uses shared PLANS dict
    if not plan:
        messages.error(request, _("തിരഞ്ഞെടുത്ത വരിസംഖ്യ പ്ലാൻ അസാധുവാണ്."))
        return redirect('reader_profile')

    context = {
        'plan_type': plan_type,
        'plan_name': plan['name'],
        'price': plan['price'],
        'payments_enabled': settings.MOCK_PAYMENTS_ENABLED,
    }
    if settings.MOCK_PAYMENTS_ENABLED:
        key = f'IK_{uuid.uuid4().hex}'
        keys = dict(request.session.get(_CHECKOUT_KEYS, {}))
        keys[key] = plan_type
        # Keep only the most recent few open checkouts (dicts keep insertion order).
        request.session[_CHECKOUT_KEYS] = dict(list(keys.items())[-_MAX_OPEN_CHECKOUTS:])
        context['idempotency_key'] = key
    return render(request, 'reader/checkout.html', context)


@login_required(login_url=settings.LOGIN_URL)
@require_POST   # ✅ Blocks GET requests entirely — no more silent redirect fallback
def process_payment(request):
    """
    Simulated payment gateway callback: records a completed MockGateway
    payment and activates the plan.

    It grants a paid plan without taking any money, so it only exists when
    MOCK_PAYMENTS_ENABLED is on (never on a public site). Replace with a
    real gateway's verified callback before enabling paid plans.
    """
    if not settings.MOCK_PAYMENTS_ENABLED:
        raise Http404()

    plan_type = request.POST.get('plan_type')
    method = request.POST.get('payment_method', 'card')

    # ✅ Look up amount server-side — never trust the client's posted amount
    plan = PLANS.get(plan_type)
    if not plan:
        messages.error(request, _("അസാധുവായ പ്ലാൻ തിരഞ്ഞെടുത്തു."))
        return redirect('reader_profile')

    amount = plan['price']
    idempotency_key = request.POST.get('idempotency_key') or ''
    already_processed = _("നിങ്ങളുടെ പേയ്‌മെന്റ് ഇതിനകം പ്രോസസ്സ് ചെയ്യപ്പെടുന്നു.")

    # A double-submit of a checkout that already went through.
    if idempotency_key and PaymentDetails.objects.filter(idempotency_key=idempotency_key).exists():
        messages.info(request, already_processed)
        return redirect('reader_profile')

    # Only keys this session's checkout page issued, for the plan it showed.
    open_checkouts = dict(request.session.get(_CHECKOUT_KEYS, {}))
    if open_checkouts.get(idempotency_key) != plan_type:
        messages.error(request, _("ചെക്ക്ഔട്ട് കാലഹരണപ്പെട്ടു. ദയവായി വീണ്ടും ശ്രമിക്കുക."))
        return redirect('reader_checkout', plan_type=plan_type)

    try:
        with transaction.atomic():
            payment = PaymentDetails.objects.create(
                gateway_name='MockGateway',
                gateway_transaction_id=f'TXN_{uuid.uuid4().hex}',
                gateway_order_id=f'ORD_{uuid.uuid4().hex}',
                idempotency_key=idempotency_key,
                payment_method=method,
                amount=amount,
                status='completed',
            )
            reader = request.user
            reader.payment_details = payment
            reader.save(update_fields=['payment_details'])
            reader.activate_subscription(plan_type)
    except IntegrityError:
        # Two concurrent submits of the same checkout: the other one won.
        messages.info(request, already_processed)
        return redirect('reader_profile')
    except Exception:
        logger.exception("Mock payment failed for reader %s", request.user.pk)
        messages.error(request, _("പേയ്‌മെന്റ് പൂർത്തിയാക്കാനായില്ല. ദയവായി വീണ്ടും ശ്രമിക്കുക."))
        return redirect('reader_profile')

    open_checkouts.pop(idempotency_key, None)
    request.session[_CHECKOUT_KEYS] = open_checkouts
    messages.success(
        request,
        _("%(plan)s പ്ലാനിലേക്ക് വിജയകരമായി വരിചേർന്നു!") % {'plan': plan['name']}
    )
    return redirect('reader_profile')


@login_required(login_url=settings.LOGIN_URL)
@require_POST   # ✅ Cancellation must be a POST action, not a GET link
def cancel_subscription(request):
    """Cancel the reader's active subscription, keeping access active until end date."""
    try:
        reader = request.user
        if reader.is_subscribed and not reader.is_cancelled:
            reader.is_cancelled = True
            reader.save(update_fields=['is_cancelled'])
            messages.success(request, _("നിങ്ങളുടെ വരിസംഖ്യയുടെ സ്വയം-പുതുക്കൽ റദ്ദാക്കി. വരിസംഖ്യ കാലാവധി തീരുന്നതുവരെ നിങ്ങൾക്ക് പ്രവേശനം തുടരും."))
        else:
            messages.info(request, _("നിങ്ങൾക്ക് റദ്ദാക്കാവുന്ന സജീവ വരിസംഖ്യ ഇല്ല."))
    except Exception:
        messages.error(request, _("വായനക്കാരന്റെ പ്രൊഫൈൽ കണ്ടെത്താനായില്ല."))

    return redirect('reader_profile')


@login_required(login_url=settings.LOGIN_URL)
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
            messages.success(request, _("നിങ്ങളുടെ താൽപ്പര്യങ്ങൾ പുതുക്കി."))
            return redirect('reader_profile')
    else:
        form = UpdateInterestsForm(instance=reader)

    return render(request, 'reader/update_interests.html', {'form': form})

@login_required(login_url=settings.LOGIN_URL)
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


@login_required(login_url=settings.LOGIN_URL)
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
@login_required(login_url=settings.LOGIN_URL)
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
        _("നിങ്ങളുടെ അക്കൗണ്ട് നിർജ്ജീവമാക്കി. ഞങ്ങളുടെ ഡാറ്റ സൂക്ഷിപ്പ് നയപ്രകാരം, "
          "നിയമപരമായ കാലയളവിനുശേഷം നിങ്ങളുടെ വ്യക്തിഗത ഡാറ്റ ശാശ്വതമായി നീക്കം ചെയ്യപ്പെടും.")
    )
    return redirect('account_deactivate')

import csv
from django.http import HttpResponse

# A cell starting with one of these is run as a formula by Excel, Sheets and
# LibreOffice ("CSV injection"), and every value here is typed by a reader.
_CSV_FORMULA_PREFIXES = ('=', '+', '-', '@', '\t', '\r')


def _csv_cell(value):
    text = '' if value is None else str(value)
    return f"'{text}" if text.startswith(_CSV_FORMULA_PREFIXES) else text


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
            _csv_cell(reader.name),
            # phone_number_encrypted is the decrypted display value — Fernet
            # decrypts on access. A validated E.164 number ("+" and digits)
            # can't carry a formula, so it is written as-is.
            reader.phone_number_encrypted,
            _csv_cell(reader.address_line_1),
            _csv_cell(reader.address_line_2),
            _csv_cell(reader.post_office),
            _csv_cell(reader.city),
            _csv_cell(reader.district),
            _csv_cell(reader.state),
            _csv_cell(reader.pincode),
            _csv_cell(reader.delivery_notes),
        ])

    return response


def _confirm_then_queue(request, *, command, title, description, action_label, done_message, list_url_name):
    """
    GET shows a confirmation page; only POST queues the management command.
    The purges are irreversible, so a link (or a crawler, or a forged
    <img src>) must never be able to start one. Always returns to a fixed
    admin page — never to the Referer.
    """
    list_url = reverse(list_url_name)
    if request.method != 'POST':
        return render(request, 'reader/admin/confirm_command.html', {
            'title': title,
            'description': description,
            'action_label': action_label,
            'cancel_url': list_url,
        })
    from django_q.tasks import async_task
    async_task('django.core.management.call_command', command)
    messages.success(request, done_message)
    return redirect(list_url)


@superuser_required
def admin_trigger_purge_deactivated(request):
    """Admin-only: anonymise deactivated readers past their grace period."""
    return _confirm_then_queue(
        request,
        command='purge_deactivated_users',
        title=_("നിർജ്ജീവമാക്കിയ അക്കൗണ്ടുകൾ അജ്ഞാതമാക്കുക"),
        description=_("ഗ്രേസ് കാലയളവ് കഴിഞ്ഞ എല്ലാ നിർജ്ജീവ അക്കൗണ്ടുകളിലെയും വ്യക്തിവിവരങ്ങൾ ശാശ്വതമായി നീക്കം ചെയ്യും. ഇത് പഴയപടിയാക്കാനാവില്ല."),
        action_label=_("അജ്ഞാതമാക്കൽ തുടങ്ങുക"),
        done_message=_("അജ്ഞാതവൽക്കരണ കമാൻഡ് പശ്ചാത്തലത്തിൽ പ്രവർത്തിക്കാൻ ക്രമീകരിച്ചു."),
        list_url_name='wagtailsnippets_reader_readeruser:list',
    )


@superuser_required
def admin_trigger_purge_expired(request):
    """Admin-only: permanently delete data past the 8-year retention period."""
    return _confirm_then_queue(
        request,
        command='purge_expired_data',
        title=_("കാലഹരണപ്പെട്ട ഡാറ്റ ശാശ്വതമായി നീക്കുക"),
        description=_("8 വർഷത്തെ സൂക്ഷിപ്പുകാലം കഴിഞ്ഞ അക്കൗണ്ടുകളും വരിസംഖ്യ ചരിത്രവും ശാശ്വതമായി ഇല്ലാതാക്കും. ഇത് പഴയപടിയാക്കാനാവില്ല."),
        action_label=_("ശാശ്വതമായി നീക്കുക"),
        done_message=_("8-വർഷ നീക്കംചെയ്യൽ കമാൻഡ് പശ്ചാത്തലത്തിൽ പ്രവർത്തിക്കാൻ ക്രമീകരിച്ചു."),
        list_url_name='wagtailsnippets_reader_subscriptionhistory:list',
    )
