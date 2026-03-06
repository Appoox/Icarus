from django.forms.widgets import TextInput
from django import forms
from wagtail import blocks

class ColorPickerWidget(TextInput):
    input_type = 'color'

class ColorPickerBlock(blocks.FieldBlock):
    def __init__(self, required=False, help_text=None, **kwargs):
        self.field = forms.CharField(widget=ColorPickerWidget, required=required, help_text=help_text)
        super().__init__(**kwargs)
