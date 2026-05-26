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

# Dynamically import the active LogEntry model (handles default or custom model configurations out of the box)
from auditlog import get_logentry_model
LogEntry = get_logentry_model()

User = get_user_model()

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
        .prefetch_related('actor__groups')  # Cached to prevent N+1 database queries when rendering roles
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
        
        # Safely compile roles, prioritizing the frozen historical record, with current fallback
        actor_roles = []
        if hasattr(entry, 'actor_roles') and entry.actor_roles:
            actor_roles = entry.actor_roles
        elif entry.actor:
            if entry.actor.is_superuser:
                actor_roles.append("Superuser")
            actor_roles.extend([group.name for group in entry.actor.groups.all()])

        model_name = (
            entry.content_type.model.replace('_', ' ').title()
            if entry.content_type else '—'
        )

        try:
            raw_changes = entry.changes_dict or {}
            display_changes = entry.changes_display_dict or {}
        except Exception:
            raw_changes = {}
            display_changes = {}

        all_changes = []
        for field, vals in display_changes.items():
            raw_val = raw_changes.get(field)
            
            # Check if this is an M2M field change recorded by django-auditlog
            if isinstance(raw_val, dict) and raw_val.get('type') == 'm2m':
                operation = raw_val.get('operation', '')
                objects = raw_val.get('objects', [])
                objects_str = ", ".join(str(obj) for obj in objects) or "—"
                
                if operation == 'add':
                    old = '—'
                    new = f'Added: {objects_str}'
                elif operation == 'remove':
                    old = f'Removed: {objects_str}'
                    new = '—'
                else:
                    old = f'{operation.title()}'
                    new = objects_str
            else:
                # Standard field change fallback
                old = str(vals[0])[:40] if vals[0] not in (None, '') else '—'
                new = str(vals[1])[:40] if vals[1] not in (None, '') else '—'

            all_changes.append({
                'field': field.replace("_", " ").title(),
                'old': old,
                'new': new,
            })

        # Split into the first 3 (initially visible) and the rest (inside dropdown)
        initial_changes = all_changes[:3]
        extra_changes = all_changes[3:]

        rows.append({
            'ts':              ts,
            'actor':           actor,
            'actor_roles':     actor_roles,  # Sent to audit_log.html
            'action':          ACTION_LABELS.get(entry.action, 'Changed'),
            'action_int':      entry.action,
            'model':           model_name,
            'model_color':     get_model_color(model_name),
            'object':          str(entry.object_repr or entry.object_id or '—'),
            'initial_changes': initial_changes,
            'extra_changes':   extra_changes,
            'more_count':      len(extra_changes),
        })

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
        'page_obj':        page_obj,
        'rows':            rows,
        'staff_users':     staff_users,
        'distinct_models': distinct_models,
        'action_labels':   ACTION_LABELS,
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
            buttons_html += f'<a href="{url}" class="button button-primary bicolor icon icon-plus custom-center">{label}</a>'
        return format_html(
            """
            <style>
                .button.custom-center::before {{
                    position: static !important;
                    margin-right: 8px !important;
                    margin-left: 0 !important;
                    display: inline-block;
                    vertical-align: middle;
                }}

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
    """Dashboard panel showing recent django-auditlog entries."""
    order = 400

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

        items_html_list = []
        for entry in entries:
            ts = entry.timestamp.astimezone(ist).strftime('%b %d, %H:%M')

            if entry.actor:
                actor = entry.actor.name or str(entry.actor.phone_number)
            else:
                actor = 'System'

            action_label = self._ACTION_LABELS.get(entry.action, 'Changed')
            action_color = self._ACTION_COLORS.get(entry.action, 'inherit')
            model_name = entry.content_type.model.replace('_', ' ').title() if entry.content_type else 'Object'

            try:
                raw_changes = entry.changes_dict or {}
                display_changes = entry.changes_display_dict or {}
            except Exception:
                raw_changes = {}
                display_changes = {}

            if display_changes:
                summary_parts = []
                for i, (field, vals) in enumerate(display_changes.items()):
                    if i >= 3:
                        summary_parts.append(format_html('…+{} more', len(display_changes) - 3))
                        break
                    
                    raw_val = raw_changes.get(field)
                    if isinstance(raw_val, dict) and raw_val.get('type') == 'm2m':
                        operation = raw_val.get('operation', '')
                        objects = raw_val.get('objects', [])
                        objects_str = ", ".join(str(obj) for obj in objects) or "—"
                        if operation == 'add':
                            new_val = f'Added: {objects_str}'
                        elif operation == 'remove':
                            new_val = f'Removed: {objects_str}'
                        else:
                            new_val = objects_str
                    else:
                        new_val = str(vals[1])[:40] if vals[1] not in (None, '') else '(None)'

                    field_title = field.replace("_", " ").title()
                    summary_parts.append(format_html('{}: {}', field_title, new_val))
                changes_html = mark_safe('<br>'.join(summary_parts))
            elif display_changes is None:
                changes_html = mark_safe('<span style="color: var(--w-color-text-meta); font-style: italic;">(Display error)</span>')
            else:
                changes_html = ''

            item_html = format_html(
                """
                <li style="padding: 12px 0; border-bottom: 1px solid var(--w-color-grey-100); display: flex; align-items: flex-start; gap: 12px;">
                    <div style="flex-shrink: 0; background: var(--w-color-grey-50); padding: 8px; border-radius: 4px; font-size: 0.8rem; font-weight: bold; color: var(--w-color-grey-600); width: 80px; text-align: center; line-height: 1.3;">
                        {}
                    </div>
                    <div style="flex: 1; min-width: 0;">
                        <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 3px;">
                            <span style="font-weight: 600;">{}</span>
                            <span style="color: {}; font-size: 0.8em; font-weight: 600; text-transform: uppercase;">{}</span>
                            <span style="color: var(--w-color-text-meta); font-size: 0.85em;">{}</span>
                        </div>
                        <div style="font-size: 0.8rem; color: var(--w-color-text-meta); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">{}</div>
                    </div>
                </li>
                """,
                ts,
                actor,
                action_color,
                action_label,
                model_name,
                changes_html,
            )
            items_html_list.append(item_html)

        if not items_html_list:
            items_html = mark_safe('<p style="color: var(--w-color-text-meta);">No activity recorded yet.</p>')
        else:
            items_html = mark_safe('\n'.join(items_html_list))

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
            items_html,
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