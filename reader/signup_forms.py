"""
Separate module on purpose: allauth.account.forms imports reader.auth_forms
(via ACCOUNT_SIGNUP_FORM_CLASS) while still defining its own classes, so
importing SignupForm back into auth_forms.py is a circular import.
ACCOUNT_FORMS is resolved lazily, so this module is safe.
"""
from allauth.account.forms import SignupForm
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _


class ReaderSignupForm(SignupForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'password1' in self.fields:
            self.fields['password1'].help_text = ""
        if 'password2' in self.fields:
            self.fields['password2'].help_text =  format_html(
                "<ul><li>{}</li><li>{}</li><li>{}</li><li>{}</li></ul>",
                # _("Your password can’t be too similar to your other personal information."),
                # _("Your password must contain at least 8 characters."),
                # _("Your password can’t be a commonly used word or string of letters."),
                # _("Your password can’t be entirely numeric."),

                _("നിങ്ങളുടെ പാസ്‌വേഡ് നിങ്ങളുടെ മറ്റ് വ്യക്തിഗത വിവരങ്ങളുമായി വളരെ സാമ്യമുള്ളതാകരുത്."),
    
                # Translation: Your password must contain at least 8 characters.
                _("നിങ്ങളുടെ പാസ്‌വേഡിൽ കുറഞ്ഞത് 8 അക്ഷരങ്ങളെങ്കിലും ഉണ്ടായിരിക്കണം."),
                
                # Translation: Your password can’t be a commonly used word or string of letters.
                _("നിങ്ങളുടെ പാസ്‌വേഡ് സാധാരണയായി ഉപയോഗിക്കുന്ന ഒരു വാക്കോ അക്ഷരങ്ങളുടെ കൂട്ടമോ ആകരുത്."),
                
                # Translation: Your password can’t be entirely numeric.
                _("നിങ്ങളുടെ പാസ്‌വേഡ് പൂർണ്ണമായും അക്കങ്ങൾ മാത്രമുള്ളതാകരുത്."),
            )