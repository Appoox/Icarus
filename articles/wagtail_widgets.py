from django.forms.widgets import TextInput, Textarea
from django import forms
from wagtail import blocks

class ColorPickerWidget(TextInput):
    input_type = 'color'

class ColorPickerBlock(blocks.FieldBlock):
    def __init__(self, required=False, help_text=None, **kwargs):
        self.field = forms.CharField(widget=ColorPickerWidget, required=required, help_text=help_text)
        super().__init__(**kwargs)


class RichTextImportTextarea(Textarea):
    """
    Textarea for the Paste & Import block.

    Carries the marker class/attribute that rich_text_import_paste.js looks for,
    and ships that JS via widget media so it loads inside the StreamField editor.
    On paste, the JS reads the clipboard's text/html payload and injects the real
    HTML into this field, so headings / paragraphs / images survive to the
    converter instead of being flattened to text/plain.
    """
    def __init__(self, attrs=None):
        base = {
            'class': 'rich-text-import-input',
            'data-rich-text-import': 'true',
            'placeholder': (
                'Paste from Word, Google Docs, or a web page, then click '
                '"Convert to Blocks". Headings, paragraphs, links, lists and '
                'images are detected automatically.'
            ),
        }
        if attrs:
            base.update(attrs)
        super().__init__(attrs=base)

    class Media:
        js = ['js/rich_text_import_paste.js']