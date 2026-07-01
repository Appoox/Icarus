import json
from django.utils.safestring import mark_safe
from wagtail import hooks
from wagtail.admin.panels import Panel
import wagtail.admin.rich_text.editors.draftail.features as draftail_features
from wagtail.admin.rich_text.converters.html_to_contentstate import InlineStyleElementHandler
from wagtail.admin.ui.tables import Column
from django.template.loader import render_to_string
from wagtail.admin.ui.components import Component
from wagtail.admin.viewsets.pages import PageListingViewSet
from django.db.models import OuterRef, Subquery
from wagtail.models import PageLogEntry
from django.apps import apps

from django.utils.html import format_html
from django.urls import reverse

# ── Rich-text colour features ──────────────────────────────────────────────

@hooks.register('register_rich_text_features')
def register_text_color_features(features):
    colors = [
        ('red', 'Red', '#E53E3E'),
        ('blue', 'Blue', '#3182CE'),
        ('green', 'Green', '#38A169'),
        ('yellow', 'Yellow', '#D69E2E'),
        ('orange', 'Orange', '#DD6B20'),
        ('purple', 'Purple', '#805AD5'),
        ('gray', 'Gray', '#718096'),
    ]

    for name, label, hex_code in colors:
        feature_name = f'color-{name}'
        type_ = f'COLOR_{name.upper()}'
        control = {
            'type': type_,
            'label': label,
            'description': f'{label} Text',
            'style': {'color': hex_code},
        }
        features.register_editor_plugin('draftail', feature_name, draftail_features.InlineStyleFeature(control))
        features.register_converter_rule('contentstate', feature_name, {
            'from_database_format': {f'span[style="color: {hex_code};"]': InlineStyleElementHandler(type_)},
            'to_database_format': {'style_map': {type_: {'element': 'span', 'props': {'style': f'color: {hex_code};'}}}},
        })
        features.default_features.append(feature_name)


@hooks.register('register_rich_text_features')
def register_alignment_features(features):
    alignments = [
        ('left', 'Left', 'left'),
        ('center', 'Center', 'center'),
        ('right', 'Right', 'right'),
        ('justify', 'Justify', 'justify'),
    ]

    for name, label, value in alignments:
        feature_name = f'align-{name}'
        type_ = f'ALIGN_{name.upper()}'
        control = {
            'type': type_,
            'label': label,
            'description': f'Align {label}',
            'icon': f'align-{name}',
        }
        features.register_editor_plugin('draftail', feature_name, draftail_features.InlineStyleFeature(control))
        features.register_converter_rule('contentstate', feature_name, {
            'from_database_format': {f'div[style="text-align: {value};"]': InlineStyleElementHandler(type_)},
            'to_database_format': {'style_map': {type_: {'element': 'div', 'props': {'style': f'text-align: {value};'}}}},
        })
        features.default_features.append(feature_name)


# ── Cover image preview panel ──────────────────────────────────────────────

class CoverImagePreviewPanel(Panel):
    class BoundPanel(Panel.BoundPanel):
        template_name = "wagtailadmin/panels/cover_image_preview_panel.html"

        def get_context_data(self, parent_context=None):
            ctx = super().get_context_data(parent_context)
            instance = self.instance
            image_url = None
            if instance and instance.pk and instance.cover_image_id:
                try:
                    from wagtail.images import get_image_model
                    from wagtail.images.shortcuts import get_rendition_or_not_found
                    img = get_image_model().objects.get(pk=instance.cover_image_id)
                    rendition = get_rendition_or_not_found(img, 'width-1200')
                    image_url = rendition.url
                except Exception:
                    pass
            ctx['image_url'] = image_url
            ctx['aspect'] = getattr(instance, 'cover_image_aspect', '2x1') if instance else '2x1'
            return ctx

@hooks.register('insert_editor_js')
def cover_image_preview_js():
    return mark_safe("""
<script>
(function () {
    var RATIOS = { '3x1': '3 / 1', '2x1': '2 / 1', '16x9': '16 / 9', '4x3': '4 / 3', '1x1': '1 / 1' };
    var LABELS = { '3x1': 'Thin strip', '2x1': 'Wide banner', '16x9': 'Widescreen', '4x3': 'Classic photo', '1x1': 'Square' };
    function getThumbUrl(chooser) {
        var img = chooser.querySelector('.chosen img, .w-image-chooser__preview img, .preview-image img');
        return img ? img.src : null;
    }
    function update(chooser, select) {
        var wrap = document.querySelector('.cover-preview-wrap');
        var viewport = document.getElementById('cover-preview-viewport');
        var previewImg = document.getElementById('cover-preview-img');
        var badge = document.getElementById('cover-preview-badge');
        if (!wrap || !viewport || !previewImg) return;
        var url = getThumbUrl(chooser);
        var ratio = select ? select.value : (viewport.dataset.aspect || '2x1');
        if (!url) { wrap.style.display = 'none'; return; }
        wrap.style.display = '';
        previewImg.src = url;
        viewport.style.aspectRatio = RATIOS[ratio] || '2 / 1';
        if (badge) badge.textContent = LABELS[ratio] || '';
    }
    function init() {
        var select = document.querySelector('[name="cover_image_aspect"]');
        var chooser = null;
        if (select) {
            var panel = select.closest('.w-panel, [data-panel], section, fieldset');
            if (!panel) panel = select.parentElement.parentElement.parentElement;
            if (panel) chooser = panel.querySelector('.image-chooser, .chooser, [data-chooser]');
        }
        if (!chooser) {
            var hidden = document.querySelector('input[name="cover_image"]');
            if (hidden) chooser = hidden.closest('.image-chooser, .chooser, [data-chooser]');
        }
        if (!chooser) return;
        var viewport = document.getElementById('cover-preview-viewport');
        if (viewport && viewport.dataset.aspect) {
            viewport.style.aspectRatio = RATIOS[viewport.dataset.aspect] || '2 / 1';
        }
        setTimeout(function () { update(chooser, select); }, 400);
        if (select) select.addEventListener('change', function () { update(chooser, select); });
        chooser.addEventListener('wagtail:chooser-chosen', function () { setTimeout(function () { update(chooser, select); }, 150); });
        new MutationObserver(function () { setTimeout(function () { update(chooser, select); }, 100); }).observe(chooser, { childList: true, subtree: true, attributes: true });
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else setTimeout(init, 500);
})();
</script>
""")


# ── Analytics columns & viewset ────────────────────────────────────────────

class HitCountColumn(Column):
    def get_value(self, instance):
        specific_instance = instance.specific
        if hasattr(specific_instance, 'hit_count'):
            return specific_instance.hit_count.hits
        return "-"

class AnalyticsColumn(Column):
    def __init__(self, name, field_name, **kwargs):
        super().__init__(name, **kwargs)
        self.field_name = field_name

    def get_value(self, instance):
        specific_instance = instance.specific
        if hasattr(specific_instance, self.field_name):
            return getattr(specific_instance, self.field_name)
        return "-"

class MainIssueColumn(Column):
    def get_value(self, instance):
        specific_instance = instance.specific
        issue = getattr(specific_instance, 'main_issue', None)
        if issue:
            url = reverse('wagtailadmin_pages:edit', args=[issue.pk])
            return format_html('<a href="{}" style="text-decoration: underline;">{}</a>', url, str(issue))
        return "-"

class LastEditedByColumn(Column):
    def get_value(self, instance):
        specific_instance = instance.specific
        revision = specific_instance.latest_revision
        
        if revision and revision.user:
            user = revision.user
            name = getattr(user, 'name', '') or getattr(user, 'email', '') or "Unknown User"
            try:
                url = reverse('wagtailusers_users:edit', args=[user.pk])
                return format_html('<a href="{}" style="text-decoration: underline;">{}</a>', url, name)
            except Exception:
                return name
                
        return "-"

class DraftArticlePageListingViewSet(PageListingViewSet):
    icon = 'draft'
    menu_label = 'Draft Articles'
    menu_order = 150
    add_to_admin_menu = True

    def get_queryset(self, request):
        return super().get_queryset(request).filter(live=False)

    @property
    def columns(self):
        base_columns = [col for col in super().columns if col.name != 'parent']
        return base_columns + [
            MainIssueColumn("main_issue__title", label="Issue", sort_key="main_issue__title"),
            LastEditedByColumn("latest_revision__user__name", label="Last Edited By", sort_key="latest_revision__user__name"),
            HitCountColumn("hit_count_generic__hits", label="Views", sort_key="hit_count_generic__hits"),
            AnalyticsColumn("read_fully_count", "read_fully_count", label="Read Fully", sort_key="read_fully_count")
        ]

class PublishedArticlePageListingViewSet(PageListingViewSet):
    icon = 'doc-full'
    menu_label = 'Published Articles'
    menu_order = 151
    add_to_admin_menu = True

    def get_queryset(self, request):
        return super().get_queryset(request).filter(live=True)

    @property
    def columns(self):
        base_columns = [col for col in super().columns if col.name != 'parent']
        return base_columns + [
            MainIssueColumn("main_issue__title", label="Issue", sort_key="main_issue__title"),
            LastEditedByColumn("latest_revision__user__name", label="Last Edited By", sort_key="latest_revision__user__name"),
            HitCountColumn("hit_count_generic__hits", label="Views", sort_key="hit_count_generic__hits"),
            AnalyticsColumn("read_fully_count", "read_fully_count", label="Read Fully", sort_key="read_fully_count")
        ]

@hooks.register('register_admin_viewset')
def register_draft_article_viewset():
    Article = apps.get_model('articles', 'Article')
    DraftArticlePageListingViewSet.model = Article
    return DraftArticlePageListingViewSet('draft_articles')

@hooks.register('register_admin_viewset')
def register_published_article_viewset():
    Article = apps.get_model('articles', 'Article')
    PublishedArticlePageListingViewSet.model = Article
    return PublishedArticlePageListingViewSet('published_articles')

# ── Tag autocomplete ───────────────────────────────────────────────────────

@hooks.register('insert_editor_js')
def register_tag_autocomplete_js():
    return mark_safe("""
    <script>
    (function() {
        document.addEventListener('keydown', function(event) {
            if (event.key === 'Enter') {
                const activeElement = document.activeElement;
                if (!activeElement) return;
                const tagWrapper = activeElement.closest('[data-controller="w-tag"], .tagit, .w-tag-input');
                if (!tagWrapper) return;
                const value = activeElement.value.trim();
                if (value.length >= 2) {
                    const menus = document.querySelectorAll('.ui-autocomplete, [role="listbox"]');
                    let visibleMenu = null;
                    for (const menu of menus) {
                        if (menu.offsetParent !== null && window.getComputedStyle(menu).display !== 'none') {
                            visibleMenu = menu;
                            break;
                        }
                    }
                    if (visibleMenu) {
                        const highlighted = visibleMenu.querySelector('.ui-state-active, .active, [aria-selected="true"]');
                        if (highlighted) return;
                        const firstItem = visibleMenu.querySelector('.ui-menu-item, [role="option"]');
                        if (firstItem) {
                            event.preventDefault();
                            event.stopPropagation();
                            const target = firstItem.querySelector('.ui-menu-item-wrapper, a, span') || firstItem;
                            target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                            target.click();
                            target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
                        }
                    }
                }
            }
        }, true);
    })();
    </script>
    """)


# ── Paste & Import block — Convert to Blocks button ───────────────────────

@hooks.register('insert_editor_js')
def richtext_import_block_js():
    return mark_safe("""
<script>
(function () {
    'use strict';
    function wordCount(text) { return (text || '').trim() ? (text.trim().split(/\\s+/).length) : 0; }
    function closestContaining(el, needle, maxDepth) {
        var node = el.parentElement;
        for (var i = 0; i < (maxDepth || 12); i++) {
            if (!node) break;
            if (node.textContent && node.textContent.indexOf(needle) !== -1) return node;
            node = node.parentElement;
        }
        return null;
    }
    function findSaveDraftButton() {
        var selectors = ['button[value="action-save-draft"]', 'button[name="action-save-draft"]', 'input[name="action-save-draft"]', 'button.action-save-draft', 'button.button--draft', '[data-action-trigger="save-draft"]'];
        for (var i = 0; i < selectors.length; i++) {
            var el = document.querySelector(selectors[i]);
            if (el) return el;
        }
        var btns = document.querySelectorAll('button[type="submit"], input[type="submit"]');
        for (var j = 0; j < btns.length; j++) {
            if ((btns[j].textContent || btns[j].value || '').toLowerCase().indexOf('draft') !== -1) return btns[j];
        }
        return null;
    }
    function instrumentTextarea(ta) {
        if (ta.dataset.rtiDone) return;
        var blockEl = closestContaining(ta, 'Paste & Import', 14);
        if (!blockEl) return;
        ta.dataset.rtiDone = '1';
        if (!blockEl.querySelector('.rti-banner')) {
            var banner = document.createElement('div');
            banner.className = 'rti-banner';
            banner.style.cssText = 'background:#fffbeb;border:1px solid #fde68a;color:#78350f;font-size:12px;padding:6px 14px;margin:0 0 4px;font-family:sans-serif;border-radius:4px;';
            banner.innerHTML = '<strong>Paste &amp; Import</strong> — paste HTML or plain text below, then click <em>Convert to Blocks</em>. The page saves as a draft and reloads with the expanded blocks.';
            ta.parentNode.insertBefore(banner, ta);
        }
        if (!blockEl.querySelector('.rti-toolbar')) {
            var toolbar = document.createElement('div');
            toolbar.className = 'rti-toolbar';
            toolbar.style.cssText = 'display:flex;align-items:center;gap:10px;padding:6px 0 8px;font-family:sans-serif;';
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'rti-convert-btn button button-small';
            btn.style.cssText = 'background:#1a7f5a;color:#fff;border:none;padding:5px 14px;border-radius:4px;font-size:13px;font-weight:600;cursor:pointer;';
            btn.textContent = '\\u26a1 Convert to Blocks';
            var meta = document.createElement('span');
            meta.className = 'rti-meta';
            meta.style.cssText = 'color:#6b7280;font-size:12px;';
            toolbar.appendChild(btn);
            toolbar.appendChild(meta);
            ta.parentNode.insertBefore(toolbar, ta);
            var update = function () { var n = wordCount(ta.value); meta.textContent = n > 0 ? n.toLocaleString() + ' words' : ''; };
            ta.addEventListener('input', update);
            setTimeout(update, 400);
            btn.addEventListener('click', function () { handleConvert(ta, blockEl, btn, meta); });
        }
    }
    function handleConvert(ta, blockEl, btn, meta) {
        if (!ta.value.trim()) {
            meta.style.color = '#dc2626';
            meta.textContent = 'Paste some content first.';
            setTimeout(function () { meta.textContent = ''; }, 4000);
            return;
        }
        var form = document.getElementById('page-edit-form') || ta.closest('form') || document.querySelector('form[method="post"]');
        if (!form) { meta.style.color = '#dc2626'; meta.textContent = 'Cannot find the edit form.'; return; }
        var blockId = '__all__';
        var uuidRe = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
        var allEls = [blockEl].concat(Array.from(blockEl.querySelectorAll('*')));
        outer: for (var i = 0; i < allEls.length; i++) {
            var ds = allEls[i].dataset;
            for (var key in ds) { if (uuidRe.test(ds[key])) { blockId = ds[key]; break outer; } }
            if (allEls[i].tagName === 'INPUT' && allEls[i].type === 'hidden') { if (uuidRe.test(allEls[i].value)) { blockId = allEls[i].value; break; } }
        }
        var hiddenField = form.querySelector('input[name="convert_block_id"]');
        if (!hiddenField) {
            hiddenField = document.createElement('input');
            hiddenField.type = 'hidden';
            hiddenField.name = 'convert_block_id';
            form.appendChild(hiddenField);
        }
        hiddenField.value = blockId;
        var saveDraftBtn = findSaveDraftButton();
        if (saveDraftBtn) {
            btn.disabled = true;
            btn.style.background = '#6b7280';
            btn.textContent = '\\u23f3 Saving\\u2026';
            meta.style.color = '#1a7f5a';
            meta.textContent = 'Page will reload with the converted blocks.';
            setTimeout(function () { saveDraftBtn.click(); }, 80);
        } else {
            btn.style.background = '#b45309';
            btn.textContent = '\\u26a0\\ufe0f Click Save Draft to convert';
            meta.style.color = '#b45309';
            meta.textContent = 'Ready. Click the Save Draft button to apply conversion.';
        }
    }
    function scan() { document.querySelectorAll('textarea').forEach(instrumentTextarea); }
    new MutationObserver(function (mutations) {
        var needsScan = false;
        mutations.forEach(function (m) { m.addedNodes.forEach(function (n) { if (n.nodeType === 1) needsScan = true; }); });
        if (needsScan) scan();
    }).observe(document.body, { childList: true, subtree: true });
    if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', function () { setTimeout(scan, 800); }); } else { setTimeout(scan, 800); }
})();
</script>
""")


# ── Custom Homepage Panel Definition: Articles ─────────────────────────────

class MostReadArticlesDashboardPanel(Component):
    order = 500  
    template_name = 'wagtailadmin/panels/most_read_articles.html'

    def get_context_data(self, parent_context):
        context = super().get_context_data(parent_context)
        Article = apps.get_model('articles', 'Article')
        from django.utils import timezone
        from datetime import timedelta
        from django.db.models import Count
        from django.contrib.contenttypes.models import ContentType
        from hitcount.models import Hit
        from wagtail.models import Page

        one_month_ago = timezone.now() - timedelta(days=30)
        article_content_type = ContentType.objects.get_for_model(Article)
        page_content_type = ContentType.objects.get_for_model(Page)

        live_article_ids = list(Article.objects.live().values_list('id', flat=True))
        live_article_pks = [str(pk) for pk in live_article_ids]

        top_hits = (
            Hit.objects.filter(
                created__gte=one_month_ago,
                hitcount__content_type__in=[article_content_type, page_content_type],
                hitcount__object_pk__in=live_article_pks
            )
            .values('hitcount__object_pk')
            .annotate(total_views=Count('id'))
            .order_by('-total_views')[:5]
        )

        hit_maps = {}
        for hit in top_hits:
            try:
                hit_maps[int(hit['hitcount__object_pk'])] = hit['total_views']
            except (ValueError, TypeError):
                continue

        articles_queryset = Article.objects.live().filter(id__in=hit_maps.keys())
        articles_list = []
        for article in articles_queryset:
            article.total_views = hit_maps.get(article.id, 0)
            articles_list.append(article)

        articles_list.sort(key=lambda x: x.total_views, reverse=True)
        if len(articles_list) < 5:
            already_included = [a.id for a in articles_list]
            extra_articles = Article.objects.live().exclude(id__in=already_included)[:5 - len(articles_list)]
            for extra in extra_articles:
                extra.total_views = 0
                articles_list.append(extra)

        context['articles'] = articles_list
        return context


# ── Custom Homepage Panel Definition: Issues ───────────────────────────────

class MostReadIssuesDashboardPanel(Component):
    order = 510  
    template_name = 'wagtailadmin/panels/most_read_issues.html'

    def get_context_data(self, parent_context):
        context = super().get_context_data(parent_context)
        from django.utils import timezone
        from datetime import timedelta
        from django.db.models import Count
        from django.contrib.contenttypes.models import ContentType
        from hitcount.models import Hit
        from wagtail.models import Page

        one_month_ago = timezone.now() - timedelta(days=30)
        Issue = apps.get_model('issue', 'Issue')
        issue_content_type = ContentType.objects.get_for_model(Issue)
        page_content_type = ContentType.objects.get_for_model(Page)

        live_issue_ids = list(Issue.objects.live().values_list('id', flat=True))
        live_issue_pks = [str(pk) for pk in live_issue_ids]

        top_hits = (
            Hit.objects.filter(
                created__gte=one_month_ago,
                hitcount__content_type__in=[issue_content_type, page_content_type],
                hitcount__object_pk__in=live_issue_pks
            )
            .values('hitcount__object_pk')
            .annotate(total_views=Count('id'))
            .order_by('-total_views')[:5]
        )

        hit_maps = {}
        for hit in top_hits:
            try:
                hit_maps[int(hit['hitcount__object_pk'])] = hit['total_views']
            except (ValueError, TypeError):
                continue

        issues_queryset = Issue.objects.live().filter(id__in=hit_maps.keys())
        issues_list = []
        for issue in issues_queryset:
            issue.total_views = hit_maps.get(issue.id, 0)
            issues_list.append(issue)

        issues_list.sort(key=lambda x: x.total_views, reverse=True)
        if len(issues_list) < 5:
            already_included = [i.id for i in issues_list]
            extra_issues = Issue.objects.live().exclude(id__in=already_included)[:5 - len(issues_list)]
            for extra in extra_issues:
                extra.total_views = 0
                issues_list.append(extra)

        context['issues'] = issues_list
        return context


# ── Custom Homepage Panel Definition: Topics ───────────────────────────────

class MostReadTopicsDashboardPanel(Component):
    order = 520  
    template_name = 'wagtailadmin/panels/most_read_topics.html'

    def get_context_data(self, parent_context):
        context = super().get_context_data(parent_context)
        from django.utils import timezone
        from datetime import timedelta
        from django.db.models import Count
        from django.contrib.contenttypes.models import ContentType
        from hitcount.models import Hit
        from wagtail.models import Page

        one_month_ago = timezone.now() - timedelta(days=30)
        Topic = apps.get_model('issue', 'Topic')
        Issue = apps.get_model('issue', 'Issue')
        Article = apps.get_model('articles', 'Article')

        article_content_type = ContentType.objects.get_for_model(Article)
        issue_content_type = ContentType.objects.get_for_model(Issue)
        page_content_type = ContentType.objects.get_for_model(Page)

        all_hits = (
            Hit.objects.filter(
                created__gte=one_month_ago,
                hitcount__content_type__in=[article_content_type, issue_content_type, page_content_type]
            )
            .values('hitcount__object_pk')
            .annotate(total_views=Count('id'))
        )

        hits_map = {}
        for h in all_hits:
            try:
                pk = int(h['hitcount__object_pk'])
                hits_map[pk] = hits_map.get(pk, 0) + h['total_views']
            except (ValueError, TypeError):
                continue

        topic_views = {}
        live_issues = Issue.objects.live().filter(id__in=hits_map.keys()).select_related('topic')
        issue_paths = {}  
        
        for issue in live_issues:
            if issue.topic_id:
                issue_paths[issue.path] = issue.topic_id
                topic_views[issue.topic_id] = topic_views.get(issue.topic_id, 0) + hits_map.get(issue.id, 0)

        live_articles = Article.objects.live().filter(id__in=hits_map.keys())
        has_direct_topic = False
        try:
            Article._meta.get_field('topic')
            has_direct_topic = True
        except Exception:
            has_direct_topic = False

        for article in live_articles:
            t_id = None
            if has_direct_topic and getattr(article, 'topic_id', None):
                t_id = article.topic_id
            else:
                parent_path = article.path[:-4]
                t_id = issue_paths.get(parent_path)
                if not t_id and parent_path:
                    parent_issue = Issue.objects.live().filter(path=parent_path).first()
                    if parent_issue and parent_issue.topic_id:
                        issue_paths[parent_path] = parent_issue.topic_id
                        t_id = parent_issue.topic_id

            if t_id:
                topic_views[t_id] = topic_views.get(t_id, 0) + hits_map.get(article.id, 0)

        all_topics = Topic.objects.all()
        topics_list = []
        for topic in all_topics:
            topic.total_views = topic_views.get(topic.id, 0)
            topics_list.append(topic)

        topics_list.sort(key=lambda x: x.total_views, reverse=True)
        context['topics'] = topics_list[:5]
        return context


# ── Wagtail Dashboard Hook Registration ──────────────────────────────────

@hooks.register('construct_homepage_panels')
def add_most_read_articles_panel(request, panels):
    panels.append(MostReadArticlesDashboardPanel())
    panels.append(MostReadIssuesDashboardPanel())
    panels.append(MostReadTopicsDashboardPanel())


@hooks.register('before_edit_page')
def notify_last_edited_by(request, page):
    if request.method == 'GET':
        from articles.models import Article
        from issue.models import Issue
        from literati.models import Literati
        from django.contrib import messages
        from django.utils.formats import localize
        
        specific_page = page.specific
        if isinstance(specific_page, (Article, Issue, Literati)):
            revision = specific_page.latest_revision
            name = None
            date_str = ""
            
            if revision and revision.user:
                user = revision.user
                name = getattr(user, 'name', '') or getattr(user, 'email', '') or getattr(user, 'username', '') or str(user)
                if revision.created_at:
                    date_str = f" on {localize(revision.created_at)}"
            elif page.owner:
                user = page.owner
                name = getattr(user, 'name', '') or getattr(user, 'email', '') or getattr(user, 'username', '') or str(user)
                if page.latest_revision_created_at:
                    date_str = f" on {localize(page.latest_revision_created_at)}"
            
            if name:
                messages.info(request, f"This page was last edited by {name}{date_str}.")