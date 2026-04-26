from django.urls import reverse
from wagtail import hooks
from wagtail.admin.ui.components import Component
from wagtail.models import Page, PageLogEntry, ModelLogEntry
from wagtail.log_actions import log
from articles.models import ArticleIndexPage
from literati.models import AuthorIndexPage
from issue.models import IssueIndexPage
from django.utils.html import format_html, mark_safe
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

User = get_user_model()
from django.db.models.signals import m2m_changed
from django.dispatch import receiver
from itertools import chain

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

class RecentActivityPanel(Component):
    order = 400

    def render_html(self, parent_context=None):
        request = parent_context.get('request') if parent_context else None
        if not request:
            return ""

        # Fetch recent logs from both Page and Model logs
        page_logs = PageLogEntry.objects.select_related('user', 'page', 'content_type').order_by('-timestamp')[:5]
        model_logs = ModelLogEntry.objects.select_related('user', 'content_type').order_by('-timestamp')[:5]

        # Combine and sort by timestamp
        all_logs = sorted(
            chain(page_logs, model_logs),
            key=lambda x: x.timestamp,
            reverse=True
        )[:10]

        items_html = ""
        for entry in all_logs:
            timestamp = entry.timestamp.strftime("%b %d, %H:%M")
            user = entry.user.get_full_name() or str(entry.user.phone_number) if entry.user else "System"
            
            # Action formatting
            action_label = entry.action.replace('wagtail.', '').title()
            
            # Object and Action formatting
            if hasattr(entry, 'page') and entry.page:
                obj_name = entry.page.title
                obj_type = entry.page.get_verbose_name()
            else:
                # For ModelLogEntry, try to find the actual object or its name
                model_class = entry.content_type.model_class() if entry.content_type else None
                obj_type = model_class._meta.verbose_name.title() if model_class else "Object"
                
                # Try to get the object representation
                obj_name = entry.label or f"ID: {entry.object_id}"
                
                # Specific refinement for User/Group actions
                if model_class and model_class.__name__ == 'User':
                    if entry.action == 'wagtail.edit':
                         action_label = "Updated profile for"
                    elif entry.action == 'wagtail.create':
                        action_label = "Created new account"
                    elif entry.action == 'wagtail.added_to_group':
                        group_name = entry.data.get('group', 'a group')
                        action_label = f"Added to group '{group_name}'"
                    elif entry.action == 'wagtail.removed_from_group':
                        group_name = entry.data.get('group', 'a group')
                        action_label = f"Removed from group '{group_name}'"
                elif model_class and model_class.__name__ == 'Group':
                    if entry.action == 'wagtail.edit':
                         action_label = "Modified settings for"
                    elif entry.action == 'wagtail.group_membership_changed':
                        action_label = "Updated members in"

            items_html += f"""
                <li style="padding: 12px 0; border-bottom: 1px solid var(--w-color-grey-100); display: flex; align-items: flex-start; gap: 12px;">
                    <div style="flex-shrink: 0; background: var(--w-color-grey-50); padding: 8px; border-radius: 4px; font-size: 0.8rem; font-weight: bold; color: var(--w-color-grey-600); width: 80px; text-align: center;">
                        {timestamp}
                    </div>
                    <div>
                        <div style="font-weight: 600;">{user} {action_label.lower()} "{obj_name}"</div>
                        <div style="font-size: 0.85rem; color: var(--w-color-white-500);">{obj_type}</div>
                    </div>
                </li>
            """

        if not items_html:
            items_html = "<p>No recent activity found.</p>"

        return format_html(
            """
            <section class="panel summary-panel">
                <div class="panel-content">
                    <h2 class="panel-title">Recent Activity</h2>
                    <ul style="list-style: none; padding: 0; margin: 0;">
                        {}
                    </ul>
                    <div style="margin-top: 15px;">
                        <a href="/admin/reports/site-history/" class="button button-secondary button-small">View Full History</a>
                    </div>
                </div>
            </section>
            """,
            mark_safe(items_html)
        )

@hooks.register('register_log_actions')
def register_log_actions(actions):
    actions.register_action('wagtail.added_to_group', 'Added to group', 'Added to group')
    actions.register_action('wagtail.removed_from_group', 'Removed from group', 'Removed from group')

@hooks.register("construct_homepage_panels")
def add_create_page_panel(request, panels):
        panels.insert(0, CreatePagePanel())
        panels.append(RecentActivityPanel())

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