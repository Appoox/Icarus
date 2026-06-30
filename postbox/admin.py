from django.contrib import admin
from .models import Postbox


@admin.register(Postbox)
class PostboxAdmin(admin.ModelAdmin):
    list_display   = [
        'feedback_type', 'truncated_feedback', 'stars',
        'user', 'page_context_short', 'submitted_at', 'is_reviewed',
    ]
    list_filter    = ['feedback_type', 'is_reviewed', 'rating', 'submitted_at']
    list_editable  = ['is_reviewed']
    search_fields  = ['feedback', 'user__name']
    date_hierarchy = 'submitted_at'
    ordering       = ['-submitted_at']
    
    # Appended image and image_preview to readonly_fields to prevent editing but allow viewing
    readonly_fields = [
        'feedback_type', 'feedback', 'page_context',
        'rating', 'user', 'submitted_at', 'image', 'image_preview'
    ]
    
    # Appended image and image_preview to control their rendering location in Django Admin
    fields = [
        'feedback_type', 'feedback', 'page_context', 'rating',
        'user', 'submitted_at', 'image', 'image_preview', 'is_reviewed', 'admin_notes',
    ]
    actions = ['mark_reviewed']

    def truncated_feedback(self, obj):
        return (obj.feedback[:90] + '…') if len(obj.feedback) > 90 else obj.feedback
    truncated_feedback.short_description = 'Feedback'

    def stars(self, obj):
        return '★' * obj.rating if obj.rating else '—'
    stars.short_description = 'Rating'

    def page_context_short(self, obj):
        if not obj.page_context:
            return '—'
        return ('…' + obj.page_context[-50:]) if len(obj.page_context) > 50 else obj.page_context
    page_context_short.short_description = 'Context'

    def has_add_permission(self, request):
        return False  # Submissions come through the website only

    @admin.action(description='Mark selected as reviewed')
    def mark_reviewed(self, request, queryset):
        queryset.update(is_reviewed=True)