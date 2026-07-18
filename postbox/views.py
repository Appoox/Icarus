from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST
from django.utils.translation import gettext_lazy as _
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from .forms import PostboxForm
from .models import Postbox, PostboxNotification, PostboxNotificationPreference

User = get_user_model()


# ── Public postbox page ──────────────────────────────────────────────────

@login_required
def postbox_page(request):
    if request.method == 'POST':
        # Added request.FILES to ensure uploaded image data is passed to the form processor
        form = PostboxForm(request.POST, request.FILES)
        if form.is_valid():
            postbox = form.save(commit=False)
            postbox.user = request.user
            postbox.save()
            _notify_staff(postbox)
            messages.success(
                request,
                _('ഫീഡ്ബാക്കിന് നന്ദി'),
            )
            return redirect('postbox:page')
    else:
        page_ctx = request.GET.get('from', '').strip()
        if not page_ctx:
            referer = request.META.get('HTTP_REFERER', '')
            if referer:
                page_ctx = referer[:500]
        form = PostboxForm(initial={
            'feedback_type': 'general',
            'page_context':  page_ctx,
        })

    previous = (
        request.user.feedback_submissions
        .order_by('-submitted_at')[:5]
    )
    return render(request, 'postbox/postbox_page.html', {
        'form':     form,
        'previous': previous,
    })


# ── Internal helper: send WS + create notification rows ──────────────────

def _notify_staff(postbox):
    """Create PostboxNotification rows and push WebSocket toasts to staff."""
    staff_users = User.objects.filter(is_staff=True)
    opted_out   = PostboxNotificationPreference.objects.filter(
        receive_notifications=False
    ).values_list('user_id', flat=True)
    recipients = staff_users.exclude(id__in=opted_out)

    submitter = (
        getattr(postbox.user, 'name', None) or str(postbox.user)
    )
    msg      = f"New {postbox.get_feedback_type_display()} from {submitter}"
    edit_url = f"/admin/postbox/edit/{postbox.pk}/"

    channel_layer = get_channel_layer()

    notifs = []
    for staff in recipients:
        notif = PostboxNotification.objects.create(
            user=staff,
            postbox=postbox,
            feedback=msg,
        )
        notifs.append(notif)
    if notifs:
        async_to_sync(channel_layer.group_send)(
            "admin_postbox_notifications",
            {
                "type": "send_postbox_notification",
                "notification": {
                    "id":      f"postbox-{postbox.pk}",
                    "feedback": msg,
                    "url":     edit_url,
                },
            },
        )


# ── Admin notification API ────────────────────────────────────────────────

def postbox_admin_notifications(request):
    """Polled on WebSocket connect to surface any missed notifications."""
    if not (request.user.is_authenticated and request.user.is_staff):
        return HttpResponseForbidden()

    notifications = PostboxNotification.objects.filter(
        user=request.user, is_read=False
    ).select_related('postbox')

    data = [
        {
            "id":      n.id,
            "feedback": n.feedback,
            "url":     f"/admin/postbox/edit/{n.postbox.pk}/",
        }
        for n in notifications
    ]
    return JsonResponse({"notifications": data, "count": len(data)})


@login_required
@require_POST
def mark_postbox_notification_read(request, pk):
    if not request.user.is_staff:
        return HttpResponseForbidden()
    try:
        notif = PostboxNotification.objects.get(pk=pk, user=request.user)
        notif.is_read = True
        notif.save(update_fields=['is_read'])
    except PostboxNotification.DoesNotExist:
        pass
    return JsonResponse({"status": "success"})