# reader/auth_forms.py

from django import forms
from allauth.account.forms import LoginForm
# SignupForm removed from import: it is defined AFTER line 266 of allauth/account/forms.py,
# which is where allauth calls base_signup_form_class() and imports this file.
# At that moment allauth.account.forms is only partially initialised, so SignupForm
# does not yet exist in its namespace → circular ImportError.
# LoginForm (defined ~line 160) and BaseSignupForm (defined ~line 170) are already 
# initialised and are safe to import. Inheriting from BaseSignupForm restores core fields.
from allauth.account.adapter import get_adapter
from django.utils import timezone
from django.utils.translation import gettext, ngettext, gettext_lazy as _

class CustomLoginForm(LoginForm):
    """
    Replicates the signup page's phone field logic for the login form,
    ensures consistent styling, and tracks failed login attempt counters.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # 1. Set up custom login (phone) field and apply robust sub-widget styling/validation
        if 'login' in self.fields:
            self.fields['login'] = get_adapter().phone_form_field(label=_("ഫോൺ നമ്പർ"))
            field = self.fields['login']
            
            # Style split widgets if present, otherwise style the single main widget
            if hasattr(field.widget, 'widgets'):
                for widget in field.widget.widgets:
                    existing_class = widget.attrs.get('class', '')
                    widget.attrs['class'] = f"{existing_class} form-input".strip()
                
                # Restrict national number input (index 1) to numeric only
                if len(field.widget.widgets) > 1:
                    field.widget.widgets[1].attrs.update({
                        'inputmode': 'numeric',
                        'pattern': '[0-9]*',
                        'oninput': "this.value = this.value.replace(/[^0-9]/g, '');"
                    })
            else:
                existing_class = field.widget.attrs.get('class', '')
                field.widget.attrs['class'] = f"{existing_class} form-input".strip()
        
        # 2. Apply classes to all other fields
        for field_name, field in self.fields.items():
            if field_name != 'login':
                if not isinstance(field.widget, forms.CheckboxInput):
                    existing_class = field.widget.attrs.get('class', '')
                    field.widget.attrs['class'] = f"{existing_class} form-input".strip()

    @property
    def failed_attempts(self):
        """
        Calculates the failed login attempt count.
        Handles both SplitPhoneNumberField inputs (login_0/login_1) and standard inputs.
        """
        # A. Reconstruct the input username/phone from bound data
        login_val = self.data.get('login')
        if not login_val and self.is_bound:
            # Safely grab the national phone number from the split widget
            login_1 = self.data.get('login_1', '').strip()
            if login_1:
                login_val = login_1

        if not login_val:
            # On a fresh GET request, if django-axes is active, check attempts by client IP
            try:
                from django.conf import settings
                if 'axes' in settings.INSTALLED_APPS:
                    from axes.helpers import get_failures
                    if hasattr(self, 'request') and self.request:
                        return get_failures(self.request)
            except Exception:
                pass
            return 0

        # B. Query the user to find the standardized database identifier.
        user = None
        try:
            from django.contrib.auth import get_user_model
            User = get_user_model()

            # Try to normalize a bare 10-digit number to E.164 (+91 prefix for India)
            normalized = login_val.strip()
            if normalized.isdigit() and len(normalized) == 10:
                normalized = f"+91{normalized}"

            # Hash the normalized number and do a single exact-match lookup
            hashed = User.hash_phone(normalized)
            user = User.objects.filter(phone_number_hash=hashed).first()

            # If no match by phone, try email fallback
            if not user:
                user = User.objects.filter(email=login_val).first()
        except Exception:
            pass

        # C. Query django-axes first (if active)
        try:
            from django.conf import settings
            if 'axes' in settings.INSTALLED_APPS:
                from axes.helpers import get_failures
                if hasattr(self, 'request') and self.request:
                    username = str(user.phone_number_encrypted) if user else login_val
                    credentials = {'username': username}
                    return get_failures(self.request, credentials)
        except Exception:
            pass

        # D. Fall back to your lightweight cache tracker
        if user:
            try:
                from django.core.cache import cache
                cache_key = f"consecutive_failed_logins_{user.pk}"
                return cache.get(cache_key, 0)
            except Exception:
                pass

        return 0

    @property
    def max_attempts(self):
        """
        Returns the maximum allowed login attempts configured for lock out.
        """
        try:
            from django.conf import settings
            if 'axes' in settings.INSTALLED_APPS:
                from axes.helpers import get_failure_limit
                if hasattr(self, 'request') and self.request:
                    return get_failure_limit(self.request)
        except Exception:
            pass
        return 5  # Fallback limit matching your custom cache signal count

    @property
    def lockout_duration_text(self):
        """
        Returns a human-readable lockout duration.
        """
        try:
            from django.conf import settings
            if 'axes' in settings.INSTALLED_APPS:
                cooloff = getattr(settings, 'AXES_COOLOFF_TIME', None)
                if cooloff:
                    if hasattr(cooloff, 'total_seconds'):
                        minutes = int(cooloff.total_seconds() / 60)
                        if minutes >= 60:
                            hours = minutes // 60
                            # Malayalam does not inflect the noun after numerals;
                            # ngettext keeps the msgid pluralisable for other languages.
                            return ngettext("%(n)d മണിക്കൂർ", "%(n)d മണിക്കൂർ", hours) % {'n': hours}
                        return ngettext("%(n)d മിനിറ്റ്", "%(n)d മിനിറ്റ്", minutes) % {'n': minutes}
                    return gettext("%(n)s മണിക്കൂർ") % {'n': cooloff}
        except Exception:
            pass
        return gettext("24 മണിക്കൂർ")  # Fallback duration matching your custom cache signal period


# MODIFIED: Changed base class from forms.Form to BaseSignupForm to securely inherit passwords and emails.
class CustomSignupForm(forms.Form):
    """
    Custom fields added to allauth's signup form via ACCOUNT_SIGNUP_FORM_CLASS (or ACCOUNT_FORMS).

    This class MUST extend BaseSignupForm, NOT forms.Form.
    Reason: By inheriting from forms.Form directly to dodge the circular import, core fields
    like email, password1, and password2 were stripped from the final template context.
    BaseSignupForm safely provides those core fields before allauth dynamically wraps it.
    """
    name = forms.CharField(
        max_length=255,
        required=True,
        widget=forms.TextInput(attrs={'placeholder': _('പൂർണ്ണ നാമം'), 'class': 'form-input'}),
    )
    
    # REQUIRED: Age assurance via a neutral birth-year question.
    #
    # This replaces the old "I am 18+" checkbox.  A checkbox labelled with the
    # passing answer tells every child exactly what to tick; asking "what year
    # were you born?" instead does not display the passing answer.  clean()
    # computes the age from this value and refuses under-18 signups (the view
    # turns that refusal into a redirect to the guardian explainer page).  The
    # birth year is used only to verify age and is NEVER persisted — see
    # clean() and signup() — so a refused signup leaves nothing behind.
    #
    # Year choices are populated in __init__ (the current year moves), which
    # also makes TypedChoiceField reject any tampered out-of-range value as a
    # plain field error without a hand-rolled range check.
    birth_year = forms.TypedChoiceField(
        required=True,
        coerce=int,
        label=_("ജനന വർഷം"),
        widget=forms.Select(attrs={'class': 'form-input'}),
        help_text=_("നിങ്ങളുടെ പ്രായം ഉറപ്പാക്കാൻ മാത്രം; ഈ വിവരം സൂക്ഷിക്കില്ല."),
    )
    
    # REQUIRED: Terms of Service agreement check
    accept_terms = forms.BooleanField(
        required=True,
        label=_("സേവന വ്യവസ്ഥകളും സ്വകാര്യതാ നയവും ഞാൻ അംഗീകരിക്കുന്നു."),
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'})
    )
    
    # OPTIONAL: Reading history tracking consent
    read_history_consent = forms.BooleanField(
        required=False,
        label=_("വ്യക്തിഗത ശുപാർശകൾക്കായി എന്റെ വായനാ ചരിത്രം ട്രാക്ക് ചെയ്യുന്നതിന് ഞാൻ സമ്മതിക്കുന്നു. (Optional)"),
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        help_text=_("ശുപാർശകൾ വേണ്ടെങ്കിൽ ഇത് ചെക്ക് ചെയ്യാതെ വിടാം.")
    )

    def clean(self):
        """
        Age assurance without retaining the answer.

        The birth year is asked as a neutral question and used here, transiently,
        only to compute age:
          * 18+  → the signup proceeds; the year is discarded (it is never
                   written to the user model — see signup()).
          * < 18 → the signup is refused and the view redirects to the
                   full-screen guardian explainer page; because no user is
                   ever created and the refused POST is discarded, nothing
                   about the attempt is persisted.

        Retaining the birth year of a refused signup would invert the data
        minimisation logic: asking is permitted (age verification is a recognised
        purpose), but keeping data from someone who never became a user is not.
        """
        cleaned_data = super().clean()
        birth_year = cleaned_data.get('birth_year')
        if birth_year is not None:
            # Year-only assurance: (current year − birth year) is the coarsest
            # honest age estimate a year-only field can yield, and the 18+ gate
            # is applied against it.  Out-of-range/tampered values never reach
            # here — TypedChoiceField already rejected them as a field error.
            #
            # code='underage' is load-bearing: ReaderSignupView.form_invalid()
            # keys on it to redirect to the full-screen guardian explainer page
            # instead of re-rendering the form.  The message below is only the
            # non-redirect fallback rendering.
            if timezone.now().year - birth_year < 18:
                raise forms.ValidationError(
                    _("18 വയസ്സ് തികയാത്തവർക്ക് അക്കൗണ്ട് സൃഷ്ടിക്കാനാവില്ല; രക്ഷിതാവിന് കുടുംബത്തിനായി അക്കൗണ്ട് എടുക്കാം."),
                    code='underage',
                )
        return cleaned_data

    def signup(self, request, user):
        """
        Save custom fields to the user model upon successful signup.

        This method is the *definitive* place to set read_history_consent_at for
        the signup flow.  The adapter's save_user() also attempts to handle it via
        form.cleaned_data, but the 'form' object passed to save_user() is allauth's
        own SignupForm — NOT this class — when using ACCOUNT_SIGNUP_FORM_CLASS.
        In that configuration read_history_consent is absent from the adapter's
        form.cleaned_data entirely, so the adapter silently does nothing.
        signup() is always called with self = this CustomSignupForm instance, so
        self.cleaned_data is always the correct and complete source.

        NOTE: tracking_consent is a @property derived from read_history_consent_at;
        it is not a writable field and must never be assigned directly.
        """
        user.name = self.cleaned_data.get('name', '')

        # Age declaration: reaching signup() means clean() already confirmed 18+
        # (an under-18 birth year raises ValidationError before any user is
        # created), so record the declaration.  The birth year itself is
        # deliberately NOT written to the user — it was collected solely to
        # verify age and is discarded.  Users who want their birth year stored
        # for demographic analytics add it later on the profile page, which
        # gates it behind its own explicit birth_year_consent.
        user.is_above_18 = True
        if not user.age_declaration_at:
            user.age_declaration_at = timezone.now()

        # FIX (Bug 1): Handle read_history_consent here — not (only) in the adapter.
        #
        # Grant path: stamp the timestamp only if not already set (the adapter may
        # have handled it correctly when using ACCOUNT_FORMS — avoid overwriting its
        # timestamp with a slightly later one).
        #
        # Revoke path: explicitly force None so that even if the adapter accidentally
        # set a stale timestamp (e.g., a misconfigured form), signup() clears it.
        # A new user who leaves the checkbox unchecked must never have tracking active.
        if self.cleaned_data.get('read_history_consent'):
            if not user.read_history_consent_at:
                # Adapter didn't handle it — set the timestamp now.
                user.read_history_consent_at = timezone.now()
        else:
            # Checkbox was left unchecked: ensure the timestamp is always None.
            user.read_history_consent_at = None

        user.save()
        return user

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Birth-year choices are built per-instance because the current year
        # moves; a class-level list would go stale in a long-lived process.
        current_year = timezone.now().year
        self.fields['birth_year'].choices = (
            [('', _("ജനന വർഷം തിരഞ്ഞെടുക്കുക"))]
            + [(year, year) for year in range(current_year, 1899, -1)]
        )

        # FIX: Dynamically inject the phone field into the dictionary using the adapter (just like CustomLoginForm)
        # so it is correctly instantiated and passed onto the template.
        if 'phone' not in self.fields:
            self.fields['phone'] = get_adapter().phone_form_field(label=_("ഫോൺ നമ്പർ"))

        if 'phone' in self.fields:
            field = self.fields['phone']
            if hasattr(field.widget, 'widgets'):
                for widget in field.widget.widgets:
                    existing_class = widget.attrs.get('class', '')
                    widget.attrs['class'] = f"{existing_class} form-input".strip()
                # Restrict national number input (index 1) to numeric only
                if len(field.widget.widgets) > 1:
                    field.widget.widgets[1].attrs.update({
                        'inputmode': 'numeric',
                        'pattern': '[0-9]*',
                        'oninput': "this.value = this.value.replace(/[^0-9]/g, '');"
                    })
            else:
                existing_class = field.widget.attrs.get('class', '')
                field.widget.attrs['class'] = f"{existing_class} form-input".strip()

        # Apply styles to all non-checkbox input fields cleanly
        for field_name, field in self.fields.items():
            if field_name != 'phone':
                if not isinstance(field.widget, forms.CheckboxInput):
                    existing_class = field.widget.attrs.get('class', '')
                    field.widget.attrs['class'] = f"{existing_class} form-input".strip()