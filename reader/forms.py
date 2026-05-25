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
    and ensure legal compliance (Consent & Age verification).
    """
    name = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={'placeholder': 'Full name', 'class': 'form-input'}),
    )
    date_of_birth = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-input'}),
        help_text="Under-18 signups require parental consent under DPDP Act."
    )
    accept_terms = forms.BooleanField(
        required=True,
        label="I agree to the Terms of Service and Privacy Policy.",
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'})
    )

    def clean_date_of_birth(self):
        dob = self.cleaned_data.get('date_of_birth')
        if dob:
            today = date.today()
            age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
            if age < 18:
                raise forms.ValidationError(
                    "You must be at least 18 years old to subscribe to Icarus independently."
                )
        return dob

    def signup(self, request, user):
        user.name = self.cleaned_data.get('name', '')
        user.date_of_birth = self.cleaned_data.get('date_of_birth')
        user.save()

class ReaderProfileEditForm(forms.ModelForm):
    """
    Allows a reader to update their profile details.
    """
    phone_number = SplitPhoneNumberField(
        region='IN',
        required=False,
    )
    care_of_number = SplitPhoneNumberField(
        region='IN',
        required=False,
    )

    class Meta:
        model = ReaderUser
        fields = (
            'name', 'email', 'phone_number', 'profile_image', 'bio', 
            'gender', 'gender_other', 'date_of_birth',
            'address_line_1', 'address_line_2', 'city', 'post_office', 'pincode', 'district', 'state',
            'care_of_name', 'care_of_number', 'care_of_district', 'care_of_meghala', 'care_of_unit'
        )
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Full name', 'class': 'form-input'}),
            'email': forms.EmailInput(attrs={'placeholder': 'Email address', 'class': 'form-input'}),
            'bio': forms.Textarea(attrs={'placeholder': 'Tell us about yourself...', 'class': 'form-input', 'rows': 3}),
            'gender': forms.Select(attrs={'class': 'form-input'}),
            'gender_other': forms.TextInput(attrs={'placeholder': 'If other, please specify', 'class': 'form-input'}),
            'date_of_birth': forms.DateInput(attrs={'type': 'date', 'class': 'form-input'}),
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

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email:
            if User.objects.filter(email=email).exclude(pk=self.instance.pk).exists():
                raise forms.ValidationError('This email is already registered.')
        return email

    def clean_phone_number(self):
        phone_number = self.cleaned_data.get('phone_number')
        if phone_number:
            if User.objects.filter(phone_number=phone_number).exclude(pk=self.instance.pk).exists():
                raise forms.ValidationError('This phone number is already registered.')
        return phone_number

    def clean_pincode(self):
        pincode = self.cleaned_data.get('pincode')
        if pincode:
            import re
            if not re.match(r'^[1-9][0-9]{5}$', pincode):
                raise forms.ValidationError('Enter a valid 6-digit Indian pincode.')
        return pincode


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
            'name', 'gender', 'gender_other', 'date_of_birth',
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
            'name', 'gender', 'gender_other', 'date_of_birth',
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