from django.utils.safestring import mark_safe
from wagtail import hooks
from wagtail.admin.panels import Panel
import wagtail.admin.rich_text.editors.draftail.features as draftail_features
from wagtail.admin.rich_text.converters.html_to_contentstate import InlineStyleElementHandler

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

        // Set initial aspect-ratio on the viewport from its data attribute
        var viewport = document.getElementById('cover-preview-viewport');
        if (viewport && viewport.dataset.aspect) {
            viewport.style.aspectRatio = RATIOS[viewport.dataset.aspect] || '2 / 1';
        }

        // Initial render
        setTimeout(function () { update(chooser, select); }, 400);

        // Shape dropdown changed
        if (select) {
            select.addEventListener('change', function () { update(chooser, select); });
        }

        // Image chosen (Wagtail 4/5+ event)
        chooser.addEventListener('wagtail:chooser-chosen', function () {
            setTimeout(function () { update(chooser, select); }, 150);
        });

        // Fallback MutationObserver for all Wagtail versions
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