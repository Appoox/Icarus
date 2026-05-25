import hashlib
from django.db.models import Q
from django.urls import reverse, path
from django.core.paginator import Paginator
from django.http import HttpResponseForbidden
from django.shortcuts import render
from wagtail import hooks
from wagtail.admin.ui.components import Component
from wagtail.log_actions import log
from articles.models import ArticleIndexPage
from literati.models import AuthorIndexPage
from issue.models import IssueIndexPage
from django.utils.html import format_html, mark_safe
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from auditlog.models import LogEntry

User = get_user_model()
from django.db.models.signals import m2m_changed
from django.dispatch import receiver

ACTION_LABELS = {0: 'Created', 1: 'Updated', 2: 'Deleted'}


def get_model_color(model_name):
    """Generates an automatic stable HSL color based on the model name.
    Ensures text remains highly legible against a balanced background."""
    if not model_name or model_name == '—':
        return '#475569'
    hash_digest = hashlib.md5(model_name.encode('utf-8')).hexdigest()
    hue = int(hash_digest, 16) % 360
    return f"hsl({hue}, 65%, 35%)"


def audit_log_list(request):
    """Paginated audit log view mounted inside the Wagtail admin."""
    # Adjusted check to include Wagtail administrative groups alongside standard staff users
    is_admin_user = (
        request.user.is_staff or 
        request.user.is_superuser or 
        request.user.groups.filter(name__in=['Editors', 'Moderators']).exists()
    )
    if not is_admin_user:
        return HttpResponseForbidden()

    from zoneinfo import ZoneInfo
    ist = ZoneInfo('Asia/Kolkata')

    qs = (
        LogEntry.objects
        .select_related('actor', 'content_type')
        .order_by('-timestamp')
    )

    # ── Filters ────────────────────────────────────────────────────────
    actor_id = request.GET.get('actor')
    action   = request.GET.get('action')
    model    = request.GET.get('model')

    if actor_id:
        qs = qs.filter(actor_id=actor_id)
    if action in ('0', '1', '2'):
        qs = qs.filter(action=int(action))
    if model:
        qs = qs.filter(content_type__model=model)

    # ── Pagination ─────────────────────────────────────────────────────
    paginator = Paginator(qs, 25)
    page_obj  = paginator.get_page(request.GET.get('page'))

    # ── Build rows ─────────────────────────────────────────────────────
    rows = []
    for entry in page_obj:
        ts = entry.timestamp.astimezone(ist).strftime('%d %b %Y, %H:%M')

        actor = (
            entry.actor.name or str(entry.actor.phone_number)
            if entry.actor else 'System'
        )
        model_name = (
            entry.content_type.model.replace('_', ' ').title()
            if entry.content_type else '—'
        )

        try:
            changes = entry.changes_display_dict or {}
        except Exception:
            changes = {}

        # Restructured into a clean array of structured dicts to fit your HTML cards
        change_records = []
        more_count = 0
        for i, (field, vals) in enumerate(changes.items()):
            if i >= 4:
                more_count = len(changes) - i
                break
            old = str(vals[0])[:40] if vals[0] not in (None, '') else '—'
            new = str(vals[1])[:40] if vals[1] not in (None, '') else '—'
            change_records.append({
                'field': field.replace("_", " ").title(),
                'old': old,
                'new': new
            })

        rows.append({
            'ts':          ts,
            'actor':       actor,
            'action':      ACTION_LABELS.get(entry.action, 'Changed'),
            'action_int':  entry.action,
            'model':       model_name,
            'model_color': get_model_color(model_name),
            'object':      str(entry.object_repr or entry.object_id or '—'),
            'changes':     change_records,
            'more_count':  more_count,
        })

    # Updated filter option queries to capture Editors and Moderators who lack explicit is_staff flags
    staff_users = (
        get_user_model().objects
        .filter(Q(is_staff=True) | Q(is_superuser=True) | Q(groups__name__in=['Editors', 'Moderators']))
        .distinct()
        .order_by('name')
    )
    distinct_models = (
        LogEntry.objects
        .select_related('content_type')
        .values_list('content_type__model', flat=True)
        .distinct()
        .order_by('content_type__model')
    )

    return render(request, 'wagtailadmin/audit_log.html', {
        'page_obj':       page_obj,
        'rows':           rows,
        'staff_users':    staff_users,
        'distinct_models': distinct_models,
        'action_labels':  ACTION_LABELS,
        'filters': {
            'actor':  actor_id or '',
            'action': action  or '',
            'model':  model   or '',
        },
    })


@hooks.register('register_admin_urls')
def register_audit_log_url():
    return [
        path('auditlog/', audit_log_list, name='icarus_audit_log'),
    ]


class CreatePagePanel(Component):
    order = 50

    def render_html(self, parent_context=None):
        # 1. Extract the request object from the context to check user permissions
        request = parent_context.get('request') if parent_context else None
        
        article_parent = ArticleIndexPage.objects.first()
        literati_parent = AuthorIndexPage.objects.first()
        issue_parent = IssueIndexPage.objects.first()

        actions = [
            ('articles', 'article', '+ പുതിയ ലേഖനം', article_parent, 'page'),
            ('literati', 'literati', '+ പുതിയ ലേഖകര്‍', literati_parent, 'page'),
            ('issue', 'issue', '+ പുതിയ ലക്കം', issue_parent, 'page'),
            ('issue', 'volume', '+ പുതിയ വാല്യം', None, 'snippet'),
            ('issue', 'topic', '+ പുതിയ വിഷയം', None, 'snippet'),
        ]

        buttons_html = ""
        for app, model, label, parent, type_ in actions:
            if type_ == 'page' and parent:
                url = reverse('wagtailadmin_pages:add', args=(app, model, parent.pk))
            elif type_ == 'snippet':
                url = reverse(f'wagtailsnippets_{app}_{model}:add')
            else:
                continue
            # The 'custom-center' class handles the internal button alignment
            buttons_html += f'<a href="{url}" class="button button-primary bicolor icon icon-plus custom-center">{label}</a>'
        return format_html(
            """
            <style>
                /* 1. Reset Wagtail's absolute positioning on the icon */
                .button.custom-center::before {{
                    position: static !important;
                    margin-right: 8px !important;
                    margin-left: 0 !important;
                    display: inline-block;
                    vertical-align: middle;
                }}

                /* 2. Force the button to behave as a center-aligned flex container */
                .button.custom-center {{
                    display: inline-flex !important;
                    align-items: center !important;
                    justify-content: center !important;
                    text-align: center !important;
                    padding: 0.5em 1.5em !important;
                    min-width: 180px;
                }}
            </style>
            
            <section class="panel summary-panel">
                <div class="panel-content">
                    <h2 class="panel-title">Quick Actions</h2>
                    <span style="display: flex; gap: 12px; flex-wrap: wrap; padding: 12px 0; justify-content: flex-start;">
                        {}
                    </div>
                </div>
            </section>
            """,
            mark_safe(buttons_html)
        )

class AuditLogDashboardPanel(Component):
    """Dashboard panel showing the 10 most recent django-auditlog entries."""
    order = 400

    # ACTION constants from django-auditlog
    _ACTION_LABELS = {0: 'Created', 1: 'Updated', 2: 'Deleted'}
    _ACTION_COLORS = {
        0: 'var(--w-color-positive)',
        1: 'var(--w-color-secondary-100)',
        2: 'var(--w-color-critical)',
    }

    def render_html(self, parent_context=None):
        from zoneinfo import ZoneInfo

        ist = ZoneInfo('Asia/Kolkata')
        entries = (
            LogEntry.objects
            .select_related('actor', 'content_type')
            .order_by('-timestamp')[:5]
        )

        items_html = ''
        for entry in entries:
            ts = entry.timestamp.astimezone(ist).strftime('%b %d, %H:%M')

            if entry.actor:
                actor = entry.actor.name or str(entry.actor.phone_number)
            else:
                actor = 'System'

            action_label = self._ACTION_LABELS.get(entry.action, 'Changed')
            action_color = self._ACTION_COLORS.get(entry.action, 'inherit')
            model_name = entry.content_type.model.replace('_', ' ').title() if entry.content_type else 'Object'

            # Build a short summary of changed fields (max 3)
            try:
                changes = entry.changes_display_dict
            except Exception:
                changes = None

            if changes:
                summary_parts = []
                for i, (field, vals) in enumerate(changes.items()):
                    if i >= 3:
                        summary_parts.append(f'…+{len(changes) - 3} more')
                        break
                    new_val = str(vals[1])[:40] if vals[1] not in (None, '') else '(None)'
                    summary_parts.append(f'{field.replace("_", " ").title()}: {new_val}')
                changes_html = '<br>'.join(summary_parts)
            elif changes is None:
                # Fallback for when changes_display_dict fails (e.g. unhashable dict error)
                changes_html = '<span style="color: var(--w-color-text-meta); font-style: italic;">(Display error)</span>'
            else:
                changes_html = ''

            items_html += f"""
                <li style="padding: 12px 0; border-bottom: 1px solid var(--w-color-grey-100); display: flex; align-items: flex-start; gap: 12px;">
                    <div style="flex-shrink: 0; background: var(--w-color-grey-50); padding: 8px; border-radius: 4px; font-size: 0.8rem; font-weight: bold; color: var(--w-color-grey-600); width: 80px; text-align: center; line-height: 1.3;">
                        {ts}
                    </div>
                    <div style="flex: 1; min-width: 0;">
                        <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 3px;">
                            <span style="font-weight: 600;">{actor}</span>
                            <span style="color: {action_color}; font-size: 0.8em; font-weight: 600; text-transform: uppercase;">{action_label}</span>
                            <span style="color: var(--w-color-text-meta); font-size: 0.85em;">{model_name}</span>
                        </div>
                        <div style="font-size: 0.8rem; color: var(--w-color-text-meta); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">{changes_html}</div>
                    </div>
                </li>
            """

        if not items_html:
            items_html = '<p style="color: var(--w-color-text-meta);">No activity recorded yet.</p>'

        audit_url = reverse('icarus_audit_log')
        return format_html(
            """
            <section class="panel summary-panel">
                <div class="panel-content">
                    <h2 class="panel-title">Recent Activity</h2>
                    <ul style="list-style: none; padding: 0; margin: 0;">
                        {}
                    </ul>
                    <div style="margin-top: 15px;">
                        <a href="{}" class="button button-secondary button-small">View Full Audit Log</a>
                    </div>
                </div>
            </section>
            """,
            mark_safe(items_html),
            audit_url,
        )

@hooks.register('register_log_actions')
def register_log_actions(actions):
    actions.register_action('wagtail.added_to_group', 'Added to group', 'Added to group')
    actions.register_action('wagtail.removed_from_group', 'Removed from group', 'Removed from group')

@hooks.register("construct_homepage_panels")
def add_create_page_panel(request, panels):
    panels.insert(0, CreatePagePanel())
    panels.append(AuditLogDashboardPanel())

# ── Custom Signals for Granular Logging ────────────────────────────────

@receiver(m2m_changed, sender=User.groups.through)
def log_user_group_change(sender, instance, action, pk_set, **kwargs):
    """
    Hooks into User-Group membership changes to create granular audit logs.
    """
    if action == "post_add":
        for group_id in pk_set:
            try:
                group = Group.objects.get(pk=group_id)
                log(instance=instance, action='wagtail.added_to_group', data={'group': group.name})
            except Group.DoesNotExist:
                pass
    elif action == "post_remove":
        for group_id in pk_set:
            try:
                group = Group.objects.get(pk=group_id)
                log(instance=instance, action='wagtail.removed_from_group', data={'group': group.name})
            except Group.DoesNotExist:
                pass