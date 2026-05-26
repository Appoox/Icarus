import json
from django.utils.safestring import mark_safe
from wagtail import hooks
from wagtail.admin.panels import Panel
import wagtail.admin.rich_text.editors.draftail.features as draftail_features
from wagtail.admin.rich_text.converters.html_to_contentstate import InlineStyleElementHandler
from wagtail.admin.ui.tables import Column
from django.template.loader import render_to_string
# from .models import Article
from wagtail.admin.ui.components import Component

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
    """
    A read-only panel that shows a live cropped preview of the cover image.
    Place it inside the Cover Image MultiFieldPanel, after the chooser fields.
    """

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
    var RATIOS = {
        '3x1':  '3 / 1',
        '2x1':  '2 / 1',
        '16x9': '16 / 9',
        '4x3':  '4 / 3',
        '1x1':  '1 / 1'
    };

    var LABELS = {
        '3x1':  'Thin strip',
        '2x1':  'Wide banner',
        '16x9': 'Widescreen',
        '4x3':  'Classic photo',
        '1x1':  'Square'
    };

    function getThumbUrl(chooser) {
        var img = chooser.querySelector('.chosen img, .w-image-chooser__preview img, .preview-image img');
        return img ? img.src : null;
    }

    function update(chooser, select) {
        var wrap     = document.querySelector('.cover-preview-wrap');
        var viewport = document.getElementById('cover-preview-viewport');
        var previewImg = document.getElementById('cover-preview-img');
        var badge    = document.getElementById('cover-preview-badge');

        if (!wrap || !viewport || !previewImg) return;

        var url   = getThumbUrl(chooser);
        var ratio = select ? select.value : (viewport.dataset.aspect || '2x1');

        if (!url) {
            wrap.style.display = 'none';
            return;
        }

        wrap.style.display = '';
        previewImg.src = url;
        viewport.style.aspectRatio = RATIOS[ratio] || '2 / 1';
        if (badge) badge.textContent = LABELS[ratio] || '';
    }

    function init() {
        var select  = document.querySelector('[name="cover_image_aspect"]');
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

        if (select) {
            select.addEventListener('change', function () { update(chooser, select); });
        }

        chooser.addEventListener('wagtail:chooser-chosen', function () {
            setTimeout(function () { update(chooser, select); }, 150);
        });

        new MutationObserver(function () {
            setTimeout(function () { update(chooser, select); }, 100);
        }).observe(chooser, { childList: true, subtree: true, attributes: true });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        setTimeout(init, 500);
    }
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


from wagtail.admin.viewsets.pages import PageListingViewSet


class ArticlePageListingViewSet(PageListingViewSet):
    icon = 'doc-full'
    menu_label = 'Articles'
    menu_order = 200
    add_to_admin_menu = True

    @property
    def columns(self):
        return super().columns + [
            HitCountColumn("hit_count", label="Views", sort_key="hit_count_generic__hits"),
            AnalyticsColumn("read_fully_count", "read_fully_count", label="Read Fully", sort_key="read_fully_count")
        ]


@hooks.register('register_admin_viewset')
def register_article_viewset():
    from .models import Article
    ArticlePageListingViewSet.model = Article
    return ArticlePageListingViewSet('articles')


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
#
# How it works:
#   1. MutationObserver watches for "Paste & Import" blocks in the stream editor.
#   2. Each import block gets a "Convert to Blocks" button above its textarea.
#   3. Clicking the button adds a hidden <input name="convert_block_id"> to the
#      page edit form and programmatically clicks "Save Draft".
#   4. ArticleForm.save() in models.py intercepts convert_block_id and converts
#      that specific block. If convert_block_id is absent (e.g. JS failed to
#      inject or the Save Draft selector missed), ArticleForm.save() falls back
#      to converting ALL rich_text_import blocks automatically.

@hooks.register('insert_editor_js')
def richtext_import_block_js():
    return mark_safe("""
<script>
(function () {
    'use strict';

    /*
     * Strategy: do NOT rely on any specific data attribute for block containers.
     * Instead, find every <textarea> inside the StreamField, walk up the DOM
     * to find its block wrapper (identified by containing the label text
     * "Paste & Import"), and inject the button there.
     *
     * This works regardless of which data-* attribute Wagtail uses for blocks.
     */

    /* ── Helpers ──────────────────────────────────────────────────────────── */

    function wordCount(text) {
        return (text || '').trim() ? (text.trim().split(/\\s+/).length) : 0;
    }

    /*
     * Walk up from `el` up to `maxDepth` levels.
     * Return the first ancestor whose textContent contains `needle`
     * AND which itself has a textContent longer than the needle
     * (i.e. it wraps the label, not just equals it).
     */
    function closestContaining(el, needle, maxDepth) {
        var node = el.parentElement;
        for (var i = 0; i < (maxDepth || 12); i++) {
            if (!node) break;
            if (node.textContent && node.textContent.indexOf(needle) !== -1) return node;
            node = node.parentElement;
        }
        return null;
    }

    /* Find the Save Draft submit button. */
    function findSaveDraftButton() {
        var selectors = [
            'button[value="action-save-draft"]',
            'button[name="action-save-draft"]',
            'input[name="action-save-draft"]',
            'button.action-save-draft',
            'button.button--draft',
            '[data-action-trigger="save-draft"]',
        ];
        for (var i = 0; i < selectors.length; i++) {
            var el = document.querySelector(selectors[i]);
            if (el) return el;
        }
        // Text fallback
        var btns = document.querySelectorAll('button[type="submit"], input[type="submit"]');
        for (var j = 0; j < btns.length; j++) {
            if ((btns[j].textContent || btns[j].value || '').toLowerCase().indexOf('draft') !== -1)
                return btns[j];
        }
        return null;
    }

    /* ── Inject UI into one textarea ──────────────────────────────────────── */

    function instrumentTextarea(ta) {
        if (ta.dataset.rtiDone) return;

        /*
         * Find the block container: walk up until we find an element whose
         * text includes "Paste & Import" (the block label rendered by Wagtail).
         * Stop before <body> / <html>.
         */
        var blockEl = closestContaining(ta, 'Paste & Import', 14);
        if (!blockEl) return;   // not an import block textarea

        /*
         * Tighten: we want the CLOSEST ancestor that contains the label,
         * not a grand-ancestor that incidentally has it.  Keep walking
         * *down* (i.e. try children of blockEl) to find a tighter wrapper.
         */
        ta.dataset.rtiDone = '1';

        /* ── Banner ──────────────────────────────────────────────────────── */
        if (!blockEl.querySelector('.rti-banner')) {
            var banner = document.createElement('div');
            banner.className = 'rti-banner';
            banner.style.cssText = [
                'background:#fffbeb',
                'border:1px solid #fde68a',
                'color:#78350f',
                'font-size:12px',
                'padding:6px 14px',
                'margin:0 0 4px',
                'font-family:sans-serif',
                'border-radius:4px',
            ].join(';');
            banner.innerHTML =
                '<strong>Paste &amp; Import</strong> — paste HTML or plain text ' +
                'below, then click <em>Convert to Blocks</em>. ' +
                'The page saves as a draft and reloads with the expanded blocks.';
            /* Insert banner just before the textarea */
            ta.parentNode.insertBefore(banner, ta);
        }

        /* ── Toolbar ─────────────────────────────────────────────────────── */
        if (!blockEl.querySelector('.rti-toolbar')) {
            var toolbar = document.createElement('div');
            toolbar.className = 'rti-toolbar';
            toolbar.style.cssText = [
                'display:flex', 'align-items:center', 'gap:10px',
                'padding:6px 0 8px', 'font-family:sans-serif',
            ].join(';');

            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'rti-convert-btn button button-small';
            btn.style.cssText = [
                'background:#1a7f5a', 'color:#fff', 'border:none',
                'padding:5px 14px', 'border-radius:4px',
                'font-size:13px', 'font-weight:600', 'cursor:pointer',
            ].join(';');
            btn.textContent = '\\u26a1 Convert to Blocks';

            var meta = document.createElement('span');
            meta.className = 'rti-meta';
            meta.style.cssText = 'color:#6b7280;font-size:12px;';

            toolbar.appendChild(btn);
            toolbar.appendChild(meta);
            ta.parentNode.insertBefore(toolbar, ta);

            /* Live word count */
            var update = function () {
                var n = wordCount(ta.value);
                meta.textContent = n > 0 ? n.toLocaleString() + ' words' : '';
            };
            ta.addEventListener('input', update);
            setTimeout(update, 400);

            btn.addEventListener('click', function () {
                handleConvert(ta, blockEl, btn, meta);
            });
        }
    }

    /* ── Conversion handler ───────────────────────────────────────────────── */

    function handleConvert(ta, blockEl, btn, meta) {
        if (!ta.value.trim()) {
            meta.style.color = '#dc2626';
            meta.textContent = 'Paste some content first.';
            setTimeout(function () { meta.textContent = ''; }, 4000);
            return;
        }

        /* Find the form */
        var form =
            document.getElementById('page-edit-form') ||
            ta.closest('form') ||
            document.querySelector('form[method="post"]');

        if (!form) {
            meta.style.color = '#dc2626';
            meta.textContent = 'Cannot find the edit form.';
            return;
        }

        /*
         * Set convert_block_id.
         * Try to find a UUID in the block element's data attributes or
         * in a hidden input near it.  Fall back to '__all__' which tells
         * ArticleForm.save() to convert every import block on the page.
         */
        var blockId = '__all__';

        /* Check every data attribute on blockEl and its children for a UUID */
        var uuidRe = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
        var allEls = [blockEl].concat(Array.from(blockEl.querySelectorAll('*')));
        outer: for (var i = 0; i < allEls.length; i++) {
            var ds = allEls[i].dataset;
            for (var key in ds) {
                if (uuidRe.test(ds[key])) { blockId = ds[key]; break outer; }
            }
            /* Also check hidden inputs */
            if (allEls[i].tagName === 'INPUT' && allEls[i].type === 'hidden') {
                if (uuidRe.test(allEls[i].value)) { blockId = allEls[i].value; break; }
            }
        }

        var hiddenField = form.querySelector('input[name="convert_block_id"]');
        if (!hiddenField) {
            hiddenField = document.createElement('input');
            hiddenField.type = 'hidden';
            hiddenField.name = 'convert_block_id';
            form.appendChild(hiddenField);
        }
        hiddenField.value = blockId;

        /* Find and click Save Draft */
        var saveDraftBtn = findSaveDraftButton();
        if (saveDraftBtn) {
            btn.disabled = true;
            btn.style.background = '#6b7280';
            btn.textContent = '\\u23f3 Saving\\u2026';
            meta.style.color = '#1a7f5a';
            meta.textContent = 'Page will reload with the converted blocks.';
            setTimeout(function () { saveDraftBtn.click(); }, 80);
        } else {
            /* Hidden field is set — tell editor to click Save Draft manually */
            btn.style.background = '#b45309';
            btn.textContent = '\\u26a0\\ufe0f Click Save Draft to convert';
            meta.style.color = '#b45309';
            meta.textContent = 'Ready. Click the Save Draft button to apply conversion.';
        }
    }

    /* ── Scan for import-block textareas ─────────────────────────────────── */

    function scan() {
        document.querySelectorAll('textarea').forEach(instrumentTextarea);
    }

    /* MutationObserver: re-scan when new blocks are added */
    new MutationObserver(function (mutations) {
        var needsScan = false;
        mutations.forEach(function (m) {
            m.addedNodes.forEach(function (n) {
                if (n.nodeType === 1) needsScan = true;
            });
        });
        if (needsScan) scan();
    }).observe(document.body, { childList: true, subtree: true });

    /* Initial scan — delayed to let Wagtail finish rendering */
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { setTimeout(scan, 800); });
    } else {
        setTimeout(scan, 800);
    }

})();
</script>
""")

# ── Custom Homepage Panel Definition ────────────────────────────────────
class MostReadArticlesDashboardPanel(Component):
    """
    A custom dashboard component panel that gathers metrics from django-hitcount
    and renders a leaderboard component directly on the Wagtail home view.
    Inheriting from Component automatically registers the internal 'media' properties.
    """
    # Determines the visual ordering sequence layout position on the dashboard screen
    order = 150  
    # Path referencing the target rendering template directly
    template_name = 'wagtailadmin/panels/most_read_articles.html'

    def get_context_data(self, parent_context):
        """
        Extends the standard parent template context dictionary, injecting 
        the database rows containing popular hit analytics.
        
        CRITICAL CHANGE: This now queries individual Hit logs within a 30-day 
        rolling window to capture true current performance rather than all-time totals.
        """
        # Fetch existing structural context from parent layout stack
        context = super().get_context_data(parent_context)
        
        # Import Django utilities and the required hitcount log tables
        from .models import Article 
        from django.utils import timezone
        from datetime import timedelta
        from django.db.models import Count
        from django.contrib.contenttypes.models import ContentType
        from hitcount.models import Hit

        # Step 1: Calculate the exact timestamp representing exactly 30 days ago
        one_month_ago = timezone.now() - timedelta(days=30)

        # Step 2: Fetch the django ContentType definition matching the Article page model
        article_content_type = ContentType.objects.get_for_model(Article)

        # Step 3: Query the Hit records directly within our 30-day timeline parameter window.
        # Group the hits by hitcount__object_pk to sum view totals per distinct article instance.
        top_hits = (
            Hit.objects.filter(
                created__gte=one_month_ago,
                hitcount__content_type=article_content_type
            )
            .values('hitcount__object_pk')
            .annotate(total_views=Count('id'))
            .order_by('-total_views')[:5]
        )

        # Step 4: Parse object primary keys safely back into an integer map index.
        # This completely avoids mixed type-matching errors in relational databases like PostgreSQL.
        hit_maps = {}
        for hit in top_hits:
            try:
                pk_int = int(hit['hitcount__object_pk'])
                hit_maps[pk_int] = hit['total_views']
            except (ValueError, TypeError):
                continue

        # Step 5: Gather matching live Article model rows in a single batch query
        articles_queryset = Article.objects.live().filter(id__in=hit_maps.keys())

        # Step 6: Assign the rolling 30-day view totals to each article object
        articles_list = []
        for article in articles_queryset:
            article.total_views = hit_maps.get(article.id, 0)
            articles_list.append(article)

        # Step 7: Re-sort the final list manually to ensure descending order sequence
        articles_list.sort(key=lambda x: x.total_views, reverse=True)

        # Append the compiled data collection directly to our template presentation layer
        context['articles'] = articles_list
        
        return context

# ── Wagtail Dashboard Hook Registration ──────────────────────────────────
@hooks.register('construct_homepage_panels')
def add_most_read_articles_panel(request, panels):
    """
    Intercepts the homepage panel collection sequence and appends our 
    custom analytics insight panel instance directly to the interface layout.
    """
    # Instantiate the component without passing request, as Component reads it from parent_context
    panels.append(MostReadArticlesDashboardPanel())