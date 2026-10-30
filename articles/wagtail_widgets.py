from django.forms.widgets import TextInput, Textarea
from django import forms
from wagtail import blocks

class ColorPickerWidget(TextInput):
    input_type = 'color'

class OptionalColourWidget(TextInput):
    """
    A colour that may be left empty.

    A bare <input type="color"> can't be empty — the browser submits
    #000000 for an untouched one — so a field where "empty" means "use the
    default" pairs the picker with a text box that holds the real value.
    Picking a colour fills the box; clearing the box clears the field.

    The box takes a colour however it is written: #1d5fc4, #FC0, fc0,
    rgb(29, 95, 196), or a name such as "gold" (the script turns names
    into hex as you type).  It is saved as #rrggbb.
    """
    template_name = 'widgets/optional_colour.html'

    def __init__(self, attrs=None, placeholder_colour='#1d5fc4'):
        self.placeholder_colour = placeholder_colour
        base = {
            'class': 'optional-colour__text',
            'maxlength': '64',
            'placeholder': placeholder_colour,
            'spellcheck': 'false',
            'autocomplete': 'off',
        }
        base.update(attrs or {})
        super().__init__(attrs=base)

    def value_from_datadict(self, data, files, name):
        from home.theme import normalise_colour
        return normalise_colour(super().value_from_datadict(data, files, name))

    def get_context(self, name, value, attrs):
        from home.theme import normalise_colour, parse_hex
        context = super().get_context(name, value, attrs)
        # The form field adds maxlength="7" (the stored length); the box has
        # to take longer ways of writing a colour, e.g. rgb(29, 95, 196).
        context['widget']['attrs']['maxlength'] = '64'
        current = normalise_colour(value) if isinstance(value, str) else ''
        context['widget']['swatch'] = current if parse_hex(current) else self.placeholder_colour
        return context

    class Media:
        js = ['js/optional_colour.js']


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