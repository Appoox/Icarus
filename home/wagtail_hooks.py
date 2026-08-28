import hashlib
import logging
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
from wagtail.admin.userbar import AddPageItem, AdminItem, ExplorePageItem
from wagtail.admin.menu import MenuItem

from auditlog import get_logentry_model
LogEntry = get_logentry_model()

User = get_user_model()

logger = logging.getLogger(__name__)

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
            entry.actor.name or str(entry.actor.phone_number_encrypted)
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
    # Imported here rather than at module level: home.layout_views imports
    # home.models, and home.models is still being set up when wagtail_hooks
    # is first loaded.
    from home import layout_views

    return [
        path('auditlog/', audit_log_list, name='icarus_audit_log'),
        path('analytics/', analytics_view, name='icarus_analytics'),
        path('homepage-layout/',
             layout_views.homepage_layout,
             name='icarus_homepage_layout'),
        path('homepage-layout/save/',
             layout_views.homepage_layout_save,
             name='icarus_homepage_layout_save'),
        path('homepage-layout/search/',
             layout_views.homepage_layout_search,
             name='icarus_homepage_layout_search'),
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
            items_html = mark_safe('<p style="color: var(--w-color-text-meta); padding: 15px;">No activity recorded yet.</p>')
        else:
            items_html = mark_safe('\n'.join(items_html_list))

        audit_url = reverse('icarus_audit_log')
        return format_html(
            """
            <style>
                .icarus-panel summary {{ list-style: none; display: flex; align-items: center; }}
                .icarus-panel summary::-webkit-details-marker {{ display: none; }}
                .icarus-panel summary::before {{
                    content: ""; display: inline-block; width: 10px; height: 10px; margin-right: 14px;
                    border-right: 2.5px solid currentColor; border-bottom: 2.5px solid currentColor;
                    transform: translateY(-2px) rotate(-45deg); transition: transform 0.2s ease-in-out;
                }}
                .icarus-panel[open] summary::before {{ transform: translateY(-4px) rotate(45deg); }}
            </style>
            
            <details class="icarus-panel" style="margin-bottom: 2rem; border: 1px solid var(--w-color-border-furniture); border-radius: 6px; background-color: var(--w-color-surface-page); box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
                <summary style="cursor: pointer; padding: 1.25rem; font-size: 1.2rem; font-weight: 600; color: var(--w-color-text-default); line-height: 1.6;">
                    Recent Activity
                </summary>
                
                <div style="border-top: 1px solid var(--w-color-border-furniture); padding: 0 1.5rem 1.5rem 1.5rem;">
                    <ul style="list-style: none; padding: 0; margin: 0;">
                        {}
                    </ul>
                    <div style="margin-top: 15px;">
                        <a href="{}" class="button button-secondary button-small">View Full Audit Log</a>
                    </div>
                </div>
            </details>
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

class CustomAdminItem(AdminItem):
    template_name = "home/userbar/custom.html"

@hooks.register('construct_wagtail_userbar')
def customize_wagtail_userbar(request, items, page):
    modified_items = []
    
    for item in items:
        # Check if the current item is the "Add child page" option.
        # If it is, we skip appending it to our new list, effectively removing it.
        if isinstance(item, AddPageItem):
            continue
            
        # Check if the current item is the default "Go to Wagtail admin" option.
        # If it is, we replace it with an instance of our CustomAdminItem.
        if isinstance(item, AdminItem):
            modified_items.append(CustomAdminItem())
            continue

        if isinstance(item, ExplorePageItem):
            continue
            
        # For all other items (like Edit Page, Accessibility, etc.), keep them as they are.
        modified_items.append(item)
        
    # Always comment your code: We must update the original list in-place 
    # using Python's slice assignment so Wagtail recognizes the changes.
    items[:] = modified_items


# ── Analytics Page ─────────────────────────────────────────────────────────

def analytics_view(request):
    """Comprehensive analytics dashboard view mounted inside the Wagtail admin."""
    is_admin_user = (
        request.user.is_staff or
        request.user.is_superuser or
        request.user.groups.filter(name__in=['Editors', 'Moderators']).exists()
    )
    if not is_admin_user:
        return HttpResponseForbidden()

    import json
    from django.utils import timezone
    from datetime import timedelta
    from django.db.models import Count, Q, Sum
    from django.db.models.functions import Coalesce
    from django.apps import apps

    now = timezone.now()
    thirty_days_ago = now - timedelta(days=30)

    ReaderUser = apps.get_model('reader', 'ReaderUser')
    PaymentDetails = apps.get_model('reader', 'PaymentDetails')
    Article = apps.get_model('articles', 'Article')
    Comment = apps.get_model('kalapila', 'Comment')

    # ── 1. Homepage Hits ───────────────────────────────────────────────────
    homepage_hits_total = 0
    homepage_hits_30d = 0
    
    hp_hits_daily = []
    hp_hits_weekly = []
    hp_hits_monthly = []
    hp_hits_yearly = []
    
    try:
        from hitcount.models import HitCount, Hit
        from django.contrib.contenttypes.models import ContentType
        from home.models import HomePage
        from django.db.models.functions import TruncDay, TruncWeek, TruncMonth, TruncYear
        
        home_page = HomePage.objects.first()
        if home_page:
            hp_ct = ContentType.objects.get_for_model(HomePage)
            hp_hc = HitCount.objects.filter(
                content_type=hp_ct, object_pk=str(home_page.pk)
            ).first()
            if hp_hc:
                homepage_hits_total = hp_hc.hits
                homepage_hits_30d = Hit.objects.filter(
                    hitcount=hp_hc, created__gte=thirty_days_ago
                ).count()
                
                seven_days_ago = now - timedelta(days=7)
                daily_qs = Hit.objects.filter(hitcount=hp_hc, created__gte=seven_days_ago) \
                    .annotate(period=TruncDay('created')) \
                    .values('period').annotate(count=Count('id')).order_by('period')
                hp_hits_daily = [{'label': entry['period'].strftime('%b %d') if entry['period'] else '', 'count': entry['count']} for entry in daily_qs]

                four_weeks_ago = now - timedelta(weeks=4)
                weekly_qs = Hit.objects.filter(hitcount=hp_hc, created__gte=four_weeks_ago) \
                    .annotate(period=TruncWeek('created')) \
                    .values('period').annotate(count=Count('id')).order_by('period')
                hp_hits_weekly = [{'label': entry['period'].strftime('W%W, %Y') if entry['period'] else '', 'count': entry['count']} for entry in weekly_qs]

                twelve_months_ago = now - timedelta(days=365)
                monthly_qs = Hit.objects.filter(hitcount=hp_hc, created__gte=twelve_months_ago) \
                    .annotate(period=TruncMonth('created')) \
                    .values('period').annotate(count=Count('id')).order_by('period')
                hp_hits_monthly = [{'label': entry['period'].strftime('%b %Y') if entry['period'] else '', 'count': entry['count']} for entry in monthly_qs]

                five_years_ago = now - timedelta(days=365*5)
                yearly_qs = Hit.objects.filter(hitcount=hp_hc, created__gte=five_years_ago) \
                    .annotate(period=TruncYear('created')) \
                    .values('period').annotate(count=Count('id')).order_by('period')
                hp_hits_yearly = [{'label': entry['period'].strftime('%Y') if entry['period'] else '', 'count': entry['count']} for entry in yearly_qs]
    except Exception as e:
        logger.warning("Analytics: homepage hit trend computation failed: %s", e)

    # ── 2 & 3. Gender & Age-Bracket Hits / Read Fully ─────────────────────
    # We join HitCount data with users who have given consent
    gender_labels = []
    gender_hits = []
    gender_reads = []
    age_labels = []
    age_hits = []
    age_reads = []
    try:
        from hitcount.models import HitCount, Hit
        from django.contrib.contenttypes.models import ContentType
        article_ct = ContentType.objects.get_for_model(Article)

        # Gender distribution of users with consent
        gender_qs = (
            ReaderUser.objects
            .filter(gender_consent_at__isnull=False)
            .exclude(gender='')
            .values('gender')
            .annotate(count=Count('id'))
            .order_by('-count')
        )
        for row in gender_qs:
            gender_labels.append(row['gender'])
            # Articles read by users of this gender
            hits_for_gender = Article.objects.filter(
                readers__gender=row['gender'],
                readers__gender_consent_at__isnull=False
            ).distinct().count()
            reads_for_gender = Article.objects.filter(
                readers__gender=row['gender'],
                readers__gender_consent_at__isnull=False,
                read_fully_count__gt=0
            ).distinct().count()
            gender_hits.append(hits_for_gender)
            gender_reads.append(reads_for_gender)

        # Age bracket distribution — birth year bounds derived dynamically from current year
        current_year = now.year
        age_brackets = [
            ('18-24', current_year - 24, current_year - 18),
            ('25-34', current_year - 34, current_year - 25),
            ('35-44', current_year - 44, current_year - 35),
            ('45-54', current_year - 54, current_year - 45),
            ('55-64', current_year - 64, current_year - 55),
            ('65+',   current_year - 120, current_year - 65),
        ]
        for label, birth_from, birth_to in age_brackets:
            qs_bracket = ReaderUser.objects.filter(
                birth_year_consent_at__isnull=False,
                birth_year__gte=birth_from,
                birth_year__lte=birth_to,
            )
            count = qs_bracket.count()
            if count > 0:
                age_labels.append(label)
                articles_in_bracket = Article.objects.filter(
                    readers__in=qs_bracket
                ).distinct().count()
                reads_in_bracket = Article.objects.filter(
                    readers__in=qs_bracket,
                    read_fully_count__gt=0
                ).distinct().count()
                age_hits.append(articles_in_bracket)
                age_reads.append(reads_in_bracket)
    except Exception:
        pass

    # ── 4. Active Churn Rate ───────────────────────────────────────────────
    active_subscribers = ReaderUser.objects.filter(
        subscription_end__gt=now
    ).exclude(subscription_plan='none').count()
    cancelled_but_active = ReaderUser.objects.filter(
        is_cancelled=True,
        subscription_end__gt=now
    ).count()
    auto_renewing = active_subscribers - cancelled_but_active
    churn_rate_pct = round((cancelled_but_active / active_subscribers * 100), 1) if active_subscribers else 0

    # ── 5. Grace Period Recovery Rate ─────────────────────────────────────
    # Users who were in grace (expired <= now < expired + 3d) and are now active
    GRACE_DAYS = 3
    grace_window_start = now - timedelta(days=GRACE_DAYS + 30)  # look back 30d
    users_in_grace = ReaderUser.objects.filter(
        subscription_end__gte=grace_window_start,
        subscription_end__lt=now,
    ).count()
    recovered_from_grace = ReaderUser.objects.filter(
        subscription_end__gte=grace_window_start,
        subscription_end__lt=now,
        is_cancelled=False,
    ).filter(subscription_end__gt=now - timedelta(days=GRACE_DAYS)).count()
    # Simpler: active users who renewed in last 30+3 days
    recently_renewed = ReaderUser.objects.filter(
        subscription_start__gte=grace_window_start,
        subscription_plan__in=['1_month', '3_months', '6_months', '1_year', '3_years', '5_years'],
    ).count()
    grace_recovery_pct = round((recently_renewed / max(users_in_grace, 1)) * 100, 1)

    # ── 6. Attention Score ─────────────────────────────────────────────────
    # Top 10 articles by attention score (read_fully_count / hits)
    attention_data = []
    try:
        from hitcount.models import HitCount
        from django.contrib.contenttypes.models import ContentType
        article_ct = ContentType.objects.get_for_model(Article)
        articles_with_hits = Article.objects.live().filter(
            read_fully_count__gt=0
        ).select_related()
        for art in articles_with_hits[:20]:
            try:
                hc = HitCount.objects.filter(
                    content_type=article_ct, object_pk=str(art.pk)
                ).first()
                hits = hc.hits if hc else 0
                if hits > 0:
                    score = round((art.read_fully_count / hits) * 100, 1)
                    attention_data.append({
                        'title': art.title[:40],
                        'score': score,
                        'hits': hits,
                        'reads': art.read_fully_count,
                    })
            except Exception:
                pass
        attention_data.sort(key=lambda x: x['score'], reverse=True)
        attention_data = attention_data[:10]
    except Exception:
        pass

    # ── 7. Paywall Conversion Efficiency ──────────────────────────────────
    # Registered users in last 30 days vs all users
    new_registrations_30d = ReaderUser.objects.filter(
        date_joined__gte=thirty_days_ago
    ).count()
    new_subscribers_30d = ReaderUser.objects.filter(
        subscription_start__gte=thirty_days_ago
    ).exclude(subscription_plan='none').count()
    paywall_conversion_pct = round(
        (new_subscribers_30d / max(new_registrations_30d, 1)) * 100, 1
    )

    # ── 8. Print Subscribers by State & District ──────────────────────────
    print_by_state = list(
        ReaderUser.objects
        .filter(is_print_subscriber=True)
        .exclude(state='')
        .values('state')
        .annotate(count=Count('id'))
        .order_by('-count')[:15]
    )
    print_by_district = list(
        ReaderUser.objects
        .filter(is_print_subscriber=True)
        .exclude(district='')
        .values('district')
        .annotate(count=Count('id'))
        .order_by('-count')[:10]
    )

    # ── 9. Data Minimization Opt-In Rates ─────────────────────────────────
    total_users = ReaderUser.objects.filter(is_active=True, is_anonymized=False).count()
    read_history_optin = ReaderUser.objects.filter(
        read_history_consent_at__isnull=False
    ).count()
    gender_optin = ReaderUser.objects.filter(
        gender_consent_at__isnull=False
    ).count()
    birth_year_optin = ReaderUser.objects.filter(
        birth_year_consent_at__isnull=False
    ).count()
    newsletter_optin = ReaderUser.objects.filter(
        newsletter_opt_in=True
    ).count()
    optin_labels = ['Read History', 'Gender', 'Birth Year', 'Newsletter']
    optin_counts = [read_history_optin, gender_optin, birth_year_optin, newsletter_optin]
    optin_pcts = [
        round(c / max(total_users, 1) * 100, 1) for c in optin_counts
    ]

    # ── 10. Deactivation Pipeline ──────────────────────────────────────────
    soft_deactivated = ReaderUser.objects.filter(
        deactivated_at__isnull=False,
        is_anonymized=False
    ).count()
    anonymized_total = ReaderUser.objects.filter(is_anonymized=True).count()
    # Retention window: 8 years — waiting to clear
    from datetime import timedelta as td
    eight_years_ago = now - td(days=2920)
    pending_purge = ReaderUser.objects.filter(
        deactivated_at__isnull=False,
        deactivated_at__lte=eight_years_ago,
        is_anonymized=False
    ).count()

    # ── 11. Transaction Failure Rate by Gateway ────────────────────────────
    payment_qs = list(
        PaymentDetails.objects
        .exclude(gateway_name='')
        .values('gateway_name', 'status')
        .annotate(count=Count('id'))
        .order_by('gateway_name', 'status')
    )
    gateway_data = {}
    for row in payment_qs:
        gw = row['gateway_name'] or 'Unknown'
        if gw not in gateway_data:
            gateway_data[gw] = {'completed': 0, 'failed': 0, 'pending': 0, 'refunded': 0}
        status = row['status']
        if status in gateway_data[gw]:
            gateway_data[gw][status] = row['count']
    gateway_names = list(gateway_data.keys())
    gateway_completed = [gateway_data[g]['completed'] for g in gateway_names]
    gateway_failed = [gateway_data[g]['failed'] for g in gateway_names]
    gateway_pending = [gateway_data[g]['pending'] for g in gateway_names]

    # ── 12. Potential Account Sharing ─────────────────────────────────────
    # Users with non-null last_login_ip — group by /24 subnet prefix
    ip_sharing_suspects = []
    try:
        from home import geoip_utils
        geo_on = geoip_utils.geoip_available()
        ip_data = (
            ReaderUser.objects
            .filter(last_login_ip__isnull=False, is_active=True)
            .values('last_login_ip')
        )
        subnet_counts = {}
        subnet_sample = {}
        for row in ip_data:
            ip = row['last_login_ip']
            # Take the first 3 octets as the /24 subnet
            parts = str(ip).split('.')
            if len(parts) >= 3:
                subnet = '.'.join(parts[:3]) + '.0/24'
                subnet_counts[subnet] = subnet_counts.get(subnet, 0) + 1
                subnet_sample.setdefault(subnet, ip)
        rows = []
        for k, v in subnet_counts.items():
            if v > 1:
                country = geoip_utils.lookup_ip(subnet_sample[k])['country'] if geo_on else ''
                rows.append({'subnet': k, 'count': v, 'country': country})
        ip_sharing_suspects = sorted(
            rows, key=lambda x: x['count'], reverse=True
        )[:10]
    except Exception:
        pass

    # ── 13. Credential Stuffing Detection ─────────────────────────────────
    # Group registration IPs by /24 subnet — multiple accounts from same subnet = risk signal
    stuffing_suspects = []
    try:
        from home import geoip_utils
        geo_on = geoip_utils.geoip_available()
        reg_ip_data = (
            ReaderUser.objects
            .filter(registration_ip__isnull=False)
            .values('registration_ip')
        )
        reg_subnet_counts = {}
        reg_subnet_sample = {}
        for row in reg_ip_data:
            ip = row['registration_ip']
            parts = str(ip).split('.')
            if len(parts) >= 3:
                subnet = '.'.join(parts[:3]) + '.0/24'
                reg_subnet_counts[subnet] = reg_subnet_counts.get(subnet, 0) + 1
                reg_subnet_sample.setdefault(subnet, ip)
        rows = []
        for k, v in reg_subnet_counts.items():
            if v > 2:
                country = geoip_utils.lookup_ip(reg_subnet_sample[k])['country'] if geo_on else ''
                rows.append({'subnet': k, 'count': v, 'country': country})
        stuffing_suspects = sorted(
            rows, key=lambda x: x['count'], reverse=True
        )[:10]
    except Exception:
        pass

    # ── 14. Community Health Index ─────────────────────────────────────────
    total_comments = Comment.objects.count()
    removed_comments = Comment.objects.filter(is_removed=True).count()
    community_health_pct = round(
        (removed_comments / max(total_comments, 1)) * 100, 1
    )
    approved_comments = Comment.objects.filter(is_approved=True, is_removed=False).count()

    # ── 15. Language Slider Usage ──────────────────────────────────────────
    # Articles with translations available
    
    has_en = Article.objects.live().exclude(title_en__exact='').exclude(title_en__isnull=True).count()
    has_hi = Article.objects.live().exclude(title_hi__exact='').exclude(title_hi__isnull=True).count()
    has_ta = Article.objects.live().exclude(title_ta__exact='').exclude(title_ta__isnull=True).count()
    
    total_articles = Article.objects.live().count()
    lang_labels = ['Malayalam (Default)', 'English', 'Hindi', 'Tamil']
    lang_counts = [total_articles, has_en, has_hi, has_ta]

    # ── 16. Moderation Backlog ─────────────────────────────────────────────
    moderation_backlog = Comment.objects.filter(is_approved=False, is_removed=False).count()

    # ── 17. Favorites ─────────────────────────────────────────────────────
    most_favorited_articles = list(
        Article.objects.live()
        .annotate(fav_count=Count('favorited_by'))
        .filter(fav_count__gt=0)
        .order_by('-fav_count')
        .values('title', 'fav_count')[:10]
    )

    # ── 18. Onboarding Friction ────────────────────────────────────────────
    total_active = ReaderUser.objects.filter(is_active=True, is_anonymized=False).count()
    missing_gender = ReaderUser.objects.filter(
        is_active=True, is_anonymized=False, gender=''
    ).count()
    missing_birth_year = ReaderUser.objects.filter(
        is_active=True, is_anonymized=False, birth_year__isnull=True
    ).count()
    missing_address = ReaderUser.objects.filter(
        is_active=True, is_anonymized=False, address_line_1=''
    ).count()
    missing_pincode = ReaderUser.objects.filter(
        is_active=True, is_anonymized=False, pincode=''
    ).count()
    friction_labels = ['Gender', 'Birth Year', 'Address', 'Pincode']
    friction_pcts = [
        round(missing_gender / max(total_active, 1) * 100, 1),
        round(missing_birth_year / max(total_active, 1) * 100, 1),
        round(missing_address / max(total_active, 1) * 100, 1),
        round(missing_pincode / max(total_active, 1) * 100, 1),
    ]

    # ── 19. Passive vs Active Reading Ratio ───────────────────────────────
    # Passive = read_articles count; Active = favorite_articles count
    passive_total = ReaderUser.objects.aggregate(
        total=Count('read_articles')
    )['total'] or 0
    active_total = ReaderUser.objects.aggregate(
        total=Count('favorite_articles')
    )['total'] or 0

    # ── 20. Retention Correlation ─────────────────────────────────────────
    # Users with bookmarks who renew vs those without
    users_with_bookmarks = ReaderUser.objects.annotate(
        fav_count=Count('favorite_articles') + Count('favorite_issues')
    ).filter(fav_count__gt=0)
    users_without_bookmarks = ReaderUser.objects.annotate(
        fav_count=Count('favorite_articles') + Count('favorite_issues')
    ).filter(fav_count=0)

    with_bm_subscribed = users_with_bookmarks.filter(
        subscription_end__gt=now
    ).count()
    with_bm_total = users_with_bookmarks.count()
    without_bm_subscribed = users_without_bookmarks.filter(
        subscription_end__gt=now
    ).count()
    without_bm_total = users_without_bookmarks.count()

    retention_with_bm = round(with_bm_subscribed / max(with_bm_total, 1) * 100, 1)
    retention_without_bm = round(without_bm_subscribed / max(without_bm_total, 1) * 100, 1)

    # ── 21. Hit sources, visitor locations & suspected-bot detection ──────
    # django-hitcount records `ip` and `user_agent` on every Hit row but the
    # dashboard never reads them. Classify the UA, geolocate the IP, and — via
    # the ASN database — flag browser-UA hits from datacenter/hosting networks as
    # suspected bots (a scraper forging a browser UA can't be caught by UA alone).
    hit_source_counts = {}   # category -> count
    top_bots = []            # [{'name', 'count'}]
    country_rows = []        # [{'country', 'count'}]
    city_rows = []           # [{'city', 'country', 'count'}]
    network_rows = []        # [{'org', 'count', 'datacenter'}]
    noisy_ips = []           # [{'ip', 'count', 'org', 'country', 'datacenter'}]
    recent_hits = []         # detail rows for the table
    hits_30d_total = 0
    geoip_ready = False
    asn_ready = False
    try:
        from hitcount.models import Hit
        from home import ua_utils, geoip_utils

        geoip_ready = geoip_utils.geoip_available()
        asn_ready = geoip_utils.asn_available()
        for cat in ua_utils.CATEGORY_ORDER:
            hit_source_counts[cat] = 0

        # -- Per-IP pass: geo + ASN org + datacenter set + noisy IPs (1 lookup/IP) --
        ip_info = {}
        datacenter_ips = set()
        country_counts = {}
        city_counts = {}
        org_counts = {}
        ip_qs = (
            Hit.objects.filter(created__gte=thirty_days_ago)
            .exclude(ip__isnull=True).exclude(ip='')
            .values('ip').annotate(count=Count('id'))
        )
        for row in ip_qs:
            ip = row['ip']
            c = row['count']
            loc = geoip_utils.lookup_ip(ip) if geoip_ready else {'country': '', 'city': ''}
            org = geoip_utils.lookup_asn(ip)['org'] if asn_ready else ''
            dc = geoip_utils.is_datacenter_org(org) if asn_ready else False
            if dc:
                datacenter_ips.add(ip)
            ip_info[ip] = {
                'count': c, 'org': org, 'datacenter': dc,
                'country': loc.get('country', ''), 'city': loc.get('city', ''),
            }
            if geoip_ready:
                country_counts[loc['country']] = country_counts.get(loc['country'], 0) + c
                if loc['city']:
                    key = (loc['city'], loc['country'])
                    city_counts[key] = city_counts.get(key, 0) + c
            if org:
                org_counts[org] = org_counts.get(org, 0) + c

        country_rows = sorted(
            [{'country': k, 'count': v} for k, v in country_counts.items()],
            key=lambda x: x['count'], reverse=True)[:15]
        city_rows = sorted(
            [{'city': k[0], 'country': k[1], 'count': v} for k, v in city_counts.items()],
            key=lambda x: x['count'], reverse=True)[:10]
        network_rows = sorted(
            [{'org': k, 'count': v, 'datacenter': geoip_utils.is_datacenter_org(k)}
             for k, v in org_counts.items()],
            key=lambda x: x['count'], reverse=True)[:12]
        noisy_ips = sorted(
            [{'ip': k, 'count': v['count'], 'org': v['org'],
              'country': v['country'], 'datacenter': v['datacenter']}
             for k, v in ip_info.items()],
            key=lambda x: x['count'], reverse=True)[:12]

        # -- Per-(UA, IP) pass: category breakdown, reclassifying datacenter IPs --
        bot_name_counts = {}
        pair_qs = (
            Hit.objects.filter(created__gte=thirty_days_ago)
            .values('user_agent', 'ip').annotate(count=Count('id'))
        )
        for row in pair_qs:
            info = ua_utils.classify_user_agent(row['user_agent'])
            cat = info['category']
            if (cat in (ua_utils.CATEGORY_HUMAN, ua_utils.CATEGORY_UNKNOWN)
                    and row['ip'] in datacenter_ips):
                cat = ua_utils.CATEGORY_SUSPECTED
            c = row['count']
            hit_source_counts[cat] = hit_source_counts.get(cat, 0) + c
            hits_30d_total += c
            if info['bot_name']:
                bot_name_counts[info['bot_name']] = bot_name_counts.get(info['bot_name'], 0) + c
        top_bots = sorted(
            [{'name': k, 'count': v} for k, v in bot_name_counts.items()],
            key=lambda x: x['count'], reverse=True)[:12]

        # -- Recent hits detail table (latest 60 within the window) --
        recent_qs = (
            Hit.objects.filter(created__gte=thirty_days_ago)
            .select_related('hitcount').order_by('-created')[:60]
        )
        for hit in recent_qs:
            info = ua_utils.classify_user_agent(hit.user_agent)
            meta = ip_info.get(hit.ip, {})
            cat = info['category']
            if (cat in (ua_utils.CATEGORY_HUMAN, ua_utils.CATEGORY_UNKNOWN)
                    and hit.ip in datacenter_ips):
                cat = ua_utils.CATEGORY_SUSPECTED
            page_title = ''
            try:
                obj = hit.hitcount.content_object
                page_title = getattr(obj, 'title', '') or (str(obj) if obj else '')
            except Exception:
                page_title = ''
            city = meta.get('city')
            country = meta.get('country')
            location = f"{city}, {country}" if (city and country) else (country or '')
            device = ' · '.join(p for p in (info['device'], info['browser'], info['os']) if p)
            recent_hits.append({
                'time': hit.created.strftime('%b %d, %H:%M') if hit.created else '',
                'page': page_title[:48],
                'category': cat,
                'category_label': ua_utils.CATEGORY_LABELS.get(cat, cat),
                'bot_name': info['bot_name'],
                'device': device,
                'location': location,
                'network': (meta.get('org') or '')[:32],
                'datacenter': meta.get('datacenter', False),
                'ip': hit.ip or '',
            })
    except Exception as e:
        logger.warning("Analytics: hit-source/geo computation failed: %s", e)

    human_hits = hit_source_counts.get('human', 0)
    suspected_hits = hit_source_counts.get('suspected_bot', 0)
    scraper_hits = hit_source_counts.get('scraper', 0)
    ai_hits = hit_source_counts.get('ai_bot', 0)
    bot_hits = (
        hit_source_counts.get('suspected_bot', 0) + hit_source_counts.get('search_bot', 0)
        + hit_source_counts.get('ai_bot', 0) + hit_source_counts.get('scraper', 0)
        + hit_source_counts.get('social', 0) + hit_source_counts.get('feed', 0)
    )
    human_pct = round(human_hits / max(hits_30d_total, 1) * 100, 1)
    bot_pct = round(bot_hits / max(hits_30d_total, 1) * 100, 1)
    # Order must match the doughnut labels/colours in analytics.html.
    hit_source_values = [
        hit_source_counts.get('human', 0),
        hit_source_counts.get('suspected_bot', 0),
        hit_source_counts.get('search_bot', 0),
        hit_source_counts.get('ai_bot', 0),
        hit_source_counts.get('scraper', 0),
        hit_source_counts.get('social', 0),
        hit_source_counts.get('feed', 0),
        hit_source_counts.get('unknown', 0),
    ]

    # Best-effort count of AI crawlers blocked by the middleware (see
    # home.ai_crawler_middleware). Process-local cache — informational only.
    ai_blocked_total = 0
    try:
        from django.core.cache import cache
        ai_blocked_total = cache.get('ai_crawler_blocked_total', 0) or 0
    except Exception:
        ai_blocked_total = 0

    context = {
        # Stat cards
        'total_users': total_users,
        'active_subscribers': active_subscribers,
        'total_articles': total_articles,
        'total_comments': total_comments,
        'moderation_backlog': moderation_backlog,
        'soft_deactivated': soft_deactivated,
        'pending_purge': pending_purge,
        'anonymized_total': anonymized_total,

        # Homepage hits
        'homepage_hits_total': homepage_hits_total,
        'homepage_hits_30d': homepage_hits_30d,
        
        'hp_hits_daily_json': json.dumps(hp_hits_daily),
        'hp_hits_weekly_json': json.dumps(hp_hits_weekly),
        'hp_hits_monthly_json': json.dumps(hp_hits_monthly),
        'hp_hits_yearly_json': json.dumps(hp_hits_yearly),

        # Gender & Age (as JSON for Chart.js)
        'gender_labels_json': json.dumps(gender_labels),
        'gender_hits_json': json.dumps(gender_hits),
        'gender_reads_json': json.dumps(gender_reads),
        'age_labels_json': json.dumps(age_labels),
        'age_hits_json': json.dumps(age_hits),
        'age_reads_json': json.dumps(age_reads),

        # Churn
        'cancelled_but_active': cancelled_but_active,
        'auto_renewing': auto_renewing,
        'churn_rate_pct': churn_rate_pct,

        # Grace
        'grace_recovery_pct': grace_recovery_pct,
        'recently_renewed': recently_renewed,
        'users_in_grace': users_in_grace,

        # Attention Score
        'attention_data_json': json.dumps(attention_data),

        # Paywall conversion
        'new_registrations_30d': new_registrations_30d,
        'new_subscribers_30d': new_subscribers_30d,
        'paywall_conversion_pct': paywall_conversion_pct,

        # Print subscribers
        'print_by_state_json': json.dumps([{'state': r['state'], 'count': r['count']} for r in print_by_state]),
        'print_by_district_json': json.dumps([{'district': r['district'], 'count': r['count']} for r in print_by_district]),
        'total_print_subscribers': ReaderUser.objects.filter(is_print_subscriber=True).count(),

        # Opt-In rates
        'optin_labels_json': json.dumps(optin_labels),
        'optin_pcts_json': json.dumps(optin_pcts),
        'optin_counts_json': json.dumps(optin_counts),

        # Payments
        'gateway_names_json': json.dumps(gateway_names),
        'gateway_completed_json': json.dumps(gateway_completed),
        'gateway_failed_json': json.dumps(gateway_failed),
        'gateway_pending_json': json.dumps(gateway_pending),

        # Security
        'ip_sharing_suspects': ip_sharing_suspects,
        'stuffing_suspects': stuffing_suspects,

        # Hit sources (human / bot / scraper) & visitor locations
        'hits_30d_total': hits_30d_total,
        'human_hits': human_hits,
        'bot_hits': bot_hits,
        'scraper_hits': scraper_hits,
        'ai_hits': ai_hits,
        'suspected_hits': suspected_hits,
        'ai_blocked_total': ai_blocked_total,
        'human_pct': human_pct,
        'bot_pct': bot_pct,
        'asn_ready': asn_ready,
        'network_rows': network_rows,
        'noisy_ips': noisy_ips,
        'hit_source_values_json': json.dumps(hit_source_values),
        'top_bots': top_bots,
        'country_rows': country_rows,
        'country_rows_json': json.dumps(country_rows),
        'city_rows': city_rows,
        'recent_hits': recent_hits,
        'geoip_ready': geoip_ready,

        # Community health
        'total_comments': total_comments,
        'removed_comments': removed_comments,
        'approved_comments': approved_comments,
        'community_health_pct': community_health_pct,

        # Language
        'lang_labels_json': json.dumps(lang_labels),
        'lang_counts_json': json.dumps(lang_counts),

        # Favorites
        'most_favorited_articles_json': json.dumps([
            {'title': a['title'][:35], 'count': a['fav_count']}
            for a in most_favorited_articles
        ]),

        # Onboarding friction
        'friction_labels_json': json.dumps(friction_labels),
        'friction_pcts_json': json.dumps(friction_pcts),

        # Passive vs active
        'passive_total': passive_total,
        'active_total': active_total,

        # Retention correlation
        'retention_with_bm': retention_with_bm,
        'retention_without_bm': retention_without_bm,
        'with_bm_total': with_bm_total,
        'without_bm_total': without_bm_total,
    }

    return render(request, 'wagtailadmin/analytics.html', context)


@hooks.register('register_admin_menu_item')
def register_analytics_menu_item():
    return MenuItem(
        'Analytics',
        reverse('icarus_analytics'),
        icon_name='view',
        order=901,
    )


class _HomepageLayoutMenuItem(MenuItem):
    """
    Only shown to users who may actually edit the homepage.

    Follows the same shape as the_librarian's _SuperuserOnlyMenuItem — a
    sidebar link that leads to a 403 is worse than no link — but gates on
    Wagtail's page permissions rather than a hardcoded group name, since
    editing the layout is editing the page.
    """

    def is_shown(self, request):
        from home.models import HomePage

        page = HomePage.objects.live().first() or HomePage.objects.first()
        if page is None:
            return False
        return page.permissions_for_user(request.user).can_edit()


@hooks.register('register_admin_menu_item')
def register_homepage_layout_menu_item():
    return _HomepageLayoutMenuItem(
        'Homepage Layout',
        reverse('icarus_homepage_layout'),
        icon_name='grip',
        order=100,
    )
