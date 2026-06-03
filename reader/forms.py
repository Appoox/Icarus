from django import forms
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from datetime import date

from .models import ReaderUser
from issue.models import Topic
from phonenumber_field.formfields import SplitPhoneNumberField

# Wagtail user forms imports
from wagtail.users.forms import UserEditForm, UserCreationForm

User = get_user_model()

class AllauthSignupForm(forms.Form):
    """
    Custom signup form for django-allauth to capture Reader-specific fields
    and ensure legal compliance (DPDP Age Consent & Terms acceptance).

    DOB is NOT collected here — it is deferred to the post-login profile edit
    flow where explicit consent is gathered.  Instead, we ask for a self-
    declaration that the user is 18 or older, which is the legally meaningful
    consent mechanism under the DPDP Act.
    """
    name = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={'placeholder': 'Full name', 'class': 'form-input'}),
    )
    is_above_18 = forms.BooleanField(
        required=True,
        label="I confirm that I am 18 years of age or older.",
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        help_text="Required for legal consent under the DPDP Act.",
    )
    accept_terms = forms.BooleanField(
        required=True,
        label="I agree to the Terms of Service and Privacy Policy.",
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'})
    )

    def signup(self, request, user):
        user.name = self.cleaned_data.get('name', '')
        user.is_above_18 = True
        user.age_declaration_at = timezone.now()
        user.save()

class ReaderProfileEditForm(forms.ModelForm):
    """
    Allows a reader to update their profile details.
    birth_year and gender are only persisted when the corresponding consent checkbox
    is checked — this satisfies DPDP explicit-consent requirements.
    Reading history is only tracked when read_history_consent is given.
    """
    # Custom non-model field — the model no longer has a single phone_number column.
    # We accept the E.164 number here, validate uniqueness via the HMAC hash,
    # and call user.set_phone() in save() to keep hash and ciphertext in sync.
    phone_number = SplitPhoneNumberField(
        region='IN',
        required=False,
    )
    care_of_number = SplitPhoneNumberField(
        region='IN',
        required=False,
    )

    # ── Transient consent checkboxes (not model fields) ──
    birth_year_consent = forms.BooleanField(      # was dob_consent
        required=False,
        label="I consent to sharing my birth year.",
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
    )
    gender_consent = forms.BooleanField(
        required=False,
        label="I consent to sharing my gender.",
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
    )
    read_history_consent = forms.BooleanField(
        required=False,
        label="I consent to my reading history being tracked to personalise my experience.",
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        help_text="If revoked, your existing reading history will be cleared.",
    )

    class Meta:
        model = ReaderUser
        fields = (
            # phone_number is intentionally absent — it is handled as a custom field above.
            'name', 'email', 'profile_image', 'bio',
            'gender', 'gender_other', 'birth_year',     # was date_of_birth
            'address_line_1', 'address_line_2', 'city', 'post_office',
            'pincode', 'district', 'state',
            'care_of_name', 'care_of_district', 'care_of_meghala', 'care_of_unit'
            # care_of_number is also a non-model field override below
        )
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Full name', 'class': 'form-input'}),
            'email': forms.EmailInput(attrs={'placeholder': 'Email address', 'class': 'form-input'}),
            'bio': forms.Textarea(attrs={'placeholder': 'Tell us about yourself...', 'class': 'form-input', 'rows': 3}),
            'gender': forms.Select(attrs={'class': 'form-input'}),
            'gender_other': forms.TextInput(attrs={'placeholder': 'If other, please specify', 'class': 'form-input'}),
            # Year only — NumberInput with a sensible range.
            'birth_year': forms.NumberInput(attrs={
                'placeholder': 'e.g. 1990',
                'class': 'form-input',
                'min': '1900',
                'max': '2099',
            }),
            'address_line_1': forms.TextInput(attrs={'placeholder': 'House / flat no., street', 'class': 'form-input'}),
            'address_line_2': forms.TextInput(attrs={'placeholder': 'Landmark, area, locality', 'class': 'form-input'}),
            'city': forms.TextInput(attrs={'placeholder': 'City / town', 'class': 'form-input'}),
            'state': forms.Select(attrs={'class': 'form-input'}),
            'pincode': forms.TextInput(attrs={'placeholder': '6-digit pincode', 'class': 'form-input', 'inputmode': 'numeric', 'maxlength': '6'}),
            'post_office': forms.TextInput(attrs={'placeholder': 'Post Office', 'class': 'form-input'}),
            'district': forms.TextInput(attrs={'placeholder': 'District', 'class': 'form-input'}),
            'care_of_name': forms.TextInput(attrs={'placeholder': 'Name of the person who added you', 'class': 'form-input'}),
            'care_of_district': forms.TextInput(attrs={'placeholder': 'District of the person who added you', 'class': 'form-input'}),
            'care_of_meghala': forms.TextInput(attrs={'placeholder': 'Meghala', 'class': 'form-input'}),
            'care_of_unit': forms.TextInput(attrs={'placeholder': 'Unit', 'class': 'form-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Apply styling and numeric restriction to the split phone widgets
        for field_name in ['phone_number', 'care_of_number']:
            if field_name in self.fields:
                field = self.fields[field_name]
                # Style all sub-widgets
                for widget in field.widget.widgets:
                    widget.attrs.update({'class': 'form-input'})

                # Restrict national number input to numeric only
                if len(field.widget.widgets) > 1:
                    field.widget.widgets[1].attrs.update({
                        'inputmode': 'numeric',
                        'pattern': '[0-9]*',
                        'oninput': "this.value = this.value.replace(/[^0-9]/g, '');"
                    })

        # Pre-populate phone input from the encrypted value
        if self.instance and self.instance.pk and self.instance.phone_number_encrypted:
            self.fields['phone_number'].initial = self.instance.phone_number_encrypted

        # Pre-check consent boxes if consent was previously given
        if self.instance and self.instance.pk:
            if self.instance.birth_year_consent_at:     # was dob_consent_at
                self.fields['birth_year_consent'].initial = True
            if self.instance.gender_consent_at:
                self.fields['gender_consent'].initial = True
            if self.instance.read_history_consent_at:
                self.fields['read_history_consent'].initial = True

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email:
            if User.objects.filter(email=email).exclude(pk=self.instance.pk).exists():
                raise forms.ValidationError('This email is already registered.')
        return email

    def clean_phone_number(self):
        phone_number = self.cleaned_data.get('phone_number')
        if phone_number:
            # Uniqueness check via HMAC hash — never compare plaintext phone numbers.
            hashed = ReaderUser.hash_phone(str(phone_number))
            if User.objects.filter(phone_number_hash=hashed).exclude(pk=self.instance.pk).exists():
                raise forms.ValidationError('This phone number is already registered.')
        return phone_number

    def clean_pincode(self):
        pincode = self.cleaned_data.get('pincode')
        if pincode:
            import re
            if not re.match(r'^[1-9][0-9]{5}$', pincode):
                raise forms.ValidationError('Enter a valid 6-digit Indian pincode.')
        return pincode

    def save(self, commit=True):
        user = super().save(commit=False)

        # ── Phone number — must be handled manually (not a model field in Meta.fields) ──
        phone_number = self.cleaned_data.get('phone_number')
        if phone_number:
            # set_phone() updates both hash and ciphertext atomically.
            user.set_phone(str(phone_number))

        # ── Birth year — gate persistence on explicit consent ──
        if not self.cleaned_data.get('birth_year_consent'):
            # Consent withheld or revoked: wipe the value and its timestamp.
            user.birth_year = None
            user.birth_year_consent_at = None   # was dob_consent_at
        elif self.cleaned_data.get('birth_year') and not user.birth_year_consent_at:
            # First time giving consent — stamp the timestamp.
            user.birth_year_consent_at = timezone.now()

        # ── Gender — gate persistence on explicit consent ──
        if not self.cleaned_data.get('gender_consent'):
            user.gender = ''
            user.gender_other = ''
            user.gender_consent_at = None
        elif self.cleaned_data.get('gender') and not user.gender_consent_at:
            user.gender_consent_at = timezone.now()

        # ── Read history — gate tracking on explicit consent ──
        if not self.cleaned_data.get('read_history_consent'):
            # Consent revoked: clear the consent timestamp AND the existing M2M rows.
            # Clearing now (before save) ensures the M2M clear uses the correct PK.
            if user.pk and user.read_history_consent_at:
                user.read_articles.clear()
            user.read_history_consent_at = None
        elif not user.read_history_consent_at:
            # First time giving consent — stamp the timestamp.
            user.read_history_consent_at = timezone.now()

        if commit:
            user.save()
        return user


class UpdateInterestsForm(forms.ModelForm):
    interested_topics = forms.ModelMultipleChoiceField(
        queryset=Topic.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'topic-checkbox',
        }),
        label='Topics You\'re Interested In',
    )

    class Meta:
        model = ReaderUser
        fields = ('interested_topics',)


class CustomUserCreationForm(UserCreationForm):
    """
    Overridden UserCreationForm for Wagtail admin.
    """
    name = forms.CharField(required=True, label=_("Full Name"))

    class Meta(UserCreationForm.Meta):
        model = User
        fields = UserCreationForm.Meta.fields | {
            'name', 'gender', 'gender_other', 'birth_year',
            'address_line_1', 'address_line_2', 'city', 'post_office', 
            'pincode', 'district', 'state', 'is_print_subscriber', 
            'print_delivery_status', 'subscription_plan'
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # 1. Make email optional
        if 'email' in self.fields:
            self.fields['email'].required = False

        # 2. Safely pop 'username' to prevent write errors on your read-only @property
        if 'username' in self.fields:
            self.fields.pop('username')
            
        # 3. Make first_name and last_name optional and set to HiddenInput
        for field_name in ('first_name', 'last_name'):
            if field_name in self.fields:
                self.fields[field_name].required = False
                self.fields[field_name].widget = forms.HiddenInput()

    def clean(self):
        cleaned_data = super().clean()
        
        # 4. Auto-populate first_name and last_name from Full Name (name)
        full_name = cleaned_data.get('name', '').strip()
        if full_name:
            parts = full_name.split(maxsplit=1)
            if len(parts) == 1:
                cleaned_data['first_name'] = parts[0]
                cleaned_data['last_name'] = ""
            else:
                cleaned_data['first_name'] = parts[0]
                cleaned_data['last_name'] = parts[1]
        else:
            cleaned_data['first_name'] = ""
            cleaned_data['last_name'] = ""
        
        return cleaned_data


class CustomUserEditForm(UserEditForm):
    """
    Overridden UserEditForm for Wagtail admin.
    """
    name = forms.CharField(required=True, label=_("Full Name"))

    class Meta(UserEditForm.Meta):
        model = User
        fields = UserEditForm.Meta.fields | {
            'name', 'gender', 'gender_other', 'birth_year',
            'address_line_1', 'address_line_2', 'city', 'post_office', 
            'pincode', 'district', 'state', 'is_print_subscriber', 
            'print_delivery_status', 'subscription_plan'
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # 1. Make email optional
        if 'email' in self.fields:
            self.fields['email'].required = False

        # 2. Safely pop 'username' to prevent write errors on your read-only @property
        if 'username' in self.fields:
            self.fields.pop('username')

        # 3. Make first_name and last_name optional and set to HiddenInput
        for field_name in ('first_name', 'last_name'):
            if field_name in self.fields:
                self.fields[field_name].required = False
                self.fields[field_name].widget = forms.HiddenInput()

    def clean(self):
        cleaned_data = super().clean()
        
        # 4. Auto-populate first_name and last_name from Full Name (name)
        full_name = cleaned_data.get('name', '').strip()
        if full_name:
            parts = full_name.split(maxsplit=1)
            if len(parts) == 1:
                cleaned_data['first_name'] = parts[0]
                cleaned_data['last_name'] = ""
            else:
                cleaned_data['first_name'] = parts[0]
                cleaned_data['last_name'] = parts[1]
        else:
            cleaned_data['first_name'] = ""
            cleaned_data['last_name'] = ""
        
        return cleaned_data


# Custom allauth forms have been moved to reader/auth_forms.py 
# to avoid circular imports during startup.