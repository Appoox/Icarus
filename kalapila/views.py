import json
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, HttpResponseForbidden, HttpResponseBadRequest
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.core.mail import send_mail
from django.conf import settings
from wagtail.models import Page, Site
from .models import Comment, CommentReport, CommentSettings, DashboardNotification, CommentNotificationPreference, UserNotification

User = get_user_model()

@login_required
def post_comment(request):
    if request.method != 'POST':
        return HttpResponseBadRequest("Only POST method allowed.")

    if not getattr(request.user, 'can_comment', True):
        return JsonResponse({
            "status": "error",
            "message": "Commenting has been disabled for your account."
        }, status=403)

    page_id = request.POST.get('page_id')
    body = request.POST.get('body', '').strip()
    parent_id = request.POST.get('parent_id')

    if not page_id or not body:
        return JsonResponse({
            "status": "error",
            "message": "Page ID and comment body are required."
        }, status=400)

    if len(body) > 3000:
        return JsonResponse({
            "status": "error",
            "message": "Comment body cannot exceed 3000 characters."
        }, status=400)

    page = get_object_or_404(Page, pk=page_id)
    
    # Resolve parent if present
    parent_comment = None
    if parent_id:
        try:
            parent_comment = Comment.objects.get(pk=parent_id, page=page)
        except Comment.DoesNotExist:
            return JsonResponse({
                "status": "error",
                "message": "Parent comment does not exist."
            }, status=400)

    # Check moderation settings
    site = Site.find_for_request(request)
    comment_settings = CommentSettings.for_site(site)
    
    is_approved = True
    if comment_settings:
        if comment_settings.global_premoderation or comment_settings.premoderated_pages.filter(pk=page.pk).exists():
            is_approved = False

    # Create the comment
    comment = Comment.objects.create(
        page=page,
        user=request.user,
        parent=parent_comment,
        body=body,
        is_approved=is_approved
    )

    # Trigger notifications for staff
    # Query all staff/superuser accounts
    staff_users = User.objects.filter(is_staff=True)
    # Exclude those who turned off notifications
    opted_out_ids = CommentNotificationPreference.objects.filter(
        receive_notifications=False
    ).values_list('user_id', flat=True)
    
    notified_staff = staff_users.exclude(id__in=opted_out_ids)

    # 1. Create Dashboard Notifications
    commenter_name = request.user.name or str(request.user.phone_number)
    for staff in notified_staff:
        DashboardNotification.objects.create(
            user=staff,
            comment=comment,
            message=f"New comment by {commenter_name} on '{page.title}'"
        )

    # 2. Send Emails
    recipient_emails = [staff.email for staff in notified_staff if staff.email]
    if recipient_emails:
        subject = f"New Comment on {page.title}"
        
        # Build direct moderation link (Snippet ViewSet admin path)
        admin_url = f"{request.scheme}://{request.get_host()}/admin/snippets/kalapila/comment/edit/{comment.pk}/"
        
        email_body = (
            f"A new comment has been posted by {commenter_name} on the page '{page.title}'.\n\n"
            f"Comment Content:\n\"{body}\"\n\n"
            f"Moderate here: {admin_url}"
        )
        try:
            send_mail(
                subject,
                email_body,
                settings.DEFAULT_FROM_EMAIL or 'noreply@icarus.com',
                recipient_emails,
                fail_silently=True
            )
        except Exception:
            pass

    if is_approved:
        # Render the newly created comment using the partial template
        html = render_to_string('kalapila/comment_single.html', {
            'comment': comment,
            'request': request
        })
        return JsonResponse({
            "status": "success",
            "html": html,
            "comment_id": comment.id,
            "parent_id": parent_id
        })
    else:
        return JsonResponse({
            "status": "pending_approval",
            "message": "Your comment is awaiting moderation before it goes live."
        })

@login_required
def delete_comment(request, pk):
    if request.method != 'POST':
        return HttpResponseBadRequest("Only POST method allowed.")

    comment = get_object_or_404(Comment, pk=pk)
    
    # Check permissions: only author or staff can delete
    if request.user != comment.user and not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({
            "status": "error",
            "message": "You do not have permission to delete this comment."
        }, status=403)

    if request.user == comment.user:
        comment._deleted_by_author = True

    comment.is_removed = True
    comment.save()
    
    return JsonResponse({
        "status": "success",
        "message": "Comment was successfully removed."
    })

@login_required
def report_comment(request, pk):
    if request.method != 'POST':
        return HttpResponseBadRequest("Only POST method allowed.")

    comment = get_object_or_404(Comment, pk=pk)
    
    # Create or get report
    report, created = CommentReport.objects.get_or_create(
        comment=comment,
        user=request.user
    )

    # Trigger admin notifications when a comment is flagged
    staff_users = User.objects.filter(is_staff=True)
    opted_out_ids = CommentNotificationPreference.objects.filter(
        receive_notifications=False
    ).values_list('user_id', flat=True)
    notified_staff = staff_users.exclude(id__in=opted_out_ids)

    reporter_name = request.user.name or str(request.user.phone_number)
    comment_author = comment.user.name or str(comment.user.phone_number)

    # 1. Create Dashboard Notification
    for staff in notified_staff:
        DashboardNotification.objects.create(
            user=staff,
            comment=comment,
            message=f"Comment by {comment_author} on '{comment.page.title}' was flagged by {reporter_name}!"
        )

    # 2. Send email notification
    recipient_emails = [staff.email for staff in notified_staff if staff.email]
    if recipient_emails:
        subject = f"Comment Flagged on {comment.page.title}"
        admin_url = f"{request.scheme}://{request.get_host()}/admin/snippets/kalapila/comment/edit/{comment.pk}/"
        email_body = (
            f"The following comment has been flagged as inappropriate by {reporter_name}.\n\n"
            f"Comment Content:\n\"{comment.body}\"\n\n"
            f"Moderate here: {admin_url}"
        )
        try:
            send_mail(
                subject,
                email_body,
                settings.DEFAULT_FROM_EMAIL or 'noreply@icarus.com',
                recipient_emails,
                fail_silently=True
            )
        except Exception:
            pass
    
    return JsonResponse({
        "status": "success",
        "message": "Comment has been reported."
    })

def admin_notifications(request):
    if not request.user.is_authenticated or not request.user.is_staff:
        return HttpResponseForbidden("Access Denied.")

    notifications = DashboardNotification.objects.filter(
        user=request.user,
        is_read=False
    ).select_related('comment', 'comment__user', 'comment__page')

    data = []
    for n in notifications:
        data.append({
            "id": n.id,
            "message": n.message,
            "url": f"/admin/comments/edit/{n.comment.pk}/"
        })

    return JsonResponse({"notifications": data})

@login_required
def mark_notification_read(request, pk):
    if not request.user.is_staff:
        return HttpResponseForbidden("Access Denied.")
        
    if request.method != 'POST':
        return HttpResponseBadRequest("Only POST method allowed.")

    notification = get_object_or_404(DashboardNotification, pk=pk, user=request.user)
    notification.is_read = True
    notification.save()
    return JsonResponse({"status": "success"})
