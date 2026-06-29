from django import forms
from .models import Postbox


class PostboxForm(forms.ModelForm):
    class Meta:
        model  = Postbox
        fields = ['feedback_type', 'rating', 'page_context', 'feedback']
        widgets = {
            # Managed by JS type-selector buttons
            'feedback_type': forms.HiddenInput(),
            # Managed by JS star buttons
            'rating':        forms.HiddenInput(),
            # Pre-filled server-side, not editable by user
            'page_context':  forms.HiddenInput(),
            'feedback': forms.Textarea(attrs={
                'rows': 6,
                'class': 'feedback-textarea',
                'maxlength': 2000,
                'placeholder': (
                    'വെബ്സൈറ്റിനെക്കുറിച്ചുള്ള നിങ്ങളുടെ അഭിപ്രായം എന്താണ്? '
                    'ബന്ധപ്പെട്ട പേജിന്റെ ലിങ്ക് ഉള്‍പ്പടെ ഇവിടെ ചേര്‍ക്കുക'
                ),
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['feedback'].label      = ''
        self.fields['rating'].required    = False
        self.fields['page_context'].required = False